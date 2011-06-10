# vim: set fileencoding=utf-8 :
from datetime import timedelta
import base64
import time
import urllib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.template.context import RequestContext
from django.template.loader import render_to_string
from django.core.validators import validate_email
from django.views.decorators.csrf import csrf_protect
from synnefo.logic.email_send import send_async

from synnefo.api.common import method_not_allowed
from synnefo.db.models import Invitations, SynnefoUser
from synnefo.logic import users

from Crypto.Cipher import AES


def process_form(request):
    errors = []
    valid_inv = filter(lambda x: x.startswith("name_"), request.POST.keys())

    for inv in valid_inv:
        (name, inv_id) = inv.split('_')

        email = ""
        name = ""
        try:
            email = request.POST['email_' + inv_id]
            name = request.POST[inv]

            validate_name(name)
            validate_email(email)

            inv = add_invitation(request.user, name, email)
            send_invitation(inv)

        except Exception as e:
            try :
                errors += ["Invitation to %s <%s> not sent. Reason: %s"%(name, email, e.messages[0])]
            except:
                errors += ["Invitation to %s <%s> not sent. Reason: %s"%(name, email, e.message)]

    respose = None
    if errors:
        data = render_to_string('invitations.html',
                                {'invitations': invitations_for_user(request),
                                 'errors': errors},
                                context_instance=RequestContext(request))
        response =  HttpResponse(data)
    else:
        response = HttpResponseRedirect("/invitations/")

    return response


def validate_name(name):
    if name is None or name.strip() == '' :
        raise ValidationError("Name is empty")

    if name.find(' ') is -1:
        raise ValidationError("Name must contain at least one space")

    return True


def invitations_for_user(request):
    invitations = []

    for inv in Invitations.objects.filter(source = request.user):
        invitation = {}

        invitation['sourcename'] = inv.source.realname
        invitation['source'] = inv.source.uniq
        invitation['targetname'] = inv.target.realname
        invitation['target'] = inv.target.uniq
        invitation['accepted'] = inv.accepted
        invitation['sent'] = inv.created

        invitations.append(invitation)

    return invitations


@csrf_protect
def inv_demux(request):

    if request.method == 'GET':
        data = render_to_string('invitations.html',
                                {'invitations': invitations_for_user(request)},
                                context_instance=RequestContext(request))
        return  HttpResponse(data)
    elif request.method == 'POST':
        return process_form(request)
    else:
        method_not_allowed(request)

def login(request):

    if not request.method == 'GET':
        method_not_allowed(request)

    key = request.GET['key']

    if key is None:
        return render_login_error("10", "Required key is missing")

    PADDING = '{'

    try :
        DecodeAES = lambda c, e: c.decrypt(base64.b64decode(e)).rstrip(PADDING)
        cipher = AES.new(settings.INVITATION_ENCR_KEY)
        decoded = DecodeAES(cipher, key)
    except Exception:
        return render_login_error("20", "Required key is invalid")

    users = SynnefoUser.objects.filter(auth_token = decoded)

    if users.count() is 0:
        return render_login_error("20", "Required key is invalid")

    user = users[0]
    invitations = Invitations.objects.filter(target = user)

    if invitations.count() is 0:
        return render_login_error("30", "Non-existent invitation")

    inv = invitations[0]

    valid = timedelta(days = settings.INVITATION_VALID_DAYS)
    valid_until = inv.created + valid

    if (time.time() -
        time.mktime(inv.created.timetuple()) -
        settings.INVITATION_VALID_DAYS * 3600) > 0:
        return render_login_error("40",
                                  "Invitation expired (was valid until %s)"%
                                  valid_until.strftime('%A, %d %B %Y'))
    #if inv.accepted == False:
    #    return render_login_error("60", "Invitation already accepted")

    inv.accepted = True
    inv.save()

    data = dict()
    data['user'] = user.realname
    data['url'] = settings.APP_INSTALL_URL

    welcome = render_to_string('welcome.html', {'data': data})

    response = HttpResponse(welcome)

    response.set_cookie('X-Auth-Token', value=user.auth_token,
                        expires = valid_until.strftime('%a, %d-%b-%Y %H:%M:%S %Z'),
                        path='/')
    response['X-Auth-Token'] = user.auth_token
    return response


def render_login_error(code, text):
    error = dict()
    error['id'] = code
    error['text'] = text

    data = render_to_string('error.html', {'error': error})

    response = HttpResponse(data)
    return response


def send_invitation(invitation):
    email = {}
    email['invitee'] = invitation.target.realname
    email['inviter'] = invitation.source.realname

    valid = timedelta(days = settings.INVITATION_VALID_DAYS)
    valid_until = invitation.created + valid
    email['valid_until'] = valid_until.strftime('%A, %d %B %Y')

    PADDING = '{'
    pad = lambda s: s + (32 - len(s) % 32) * PADDING
    EncodeAES = lambda c, s: base64.b64encode(c.encrypt(pad(s)))

    cipher = AES.new(settings.INVITATION_ENCR_KEY)
    encoded = EncodeAES(cipher, invitation.target.auth_token)

    url_safe = urllib.urlencode({'key': encoded})

    email['url'] = settings.APP_INSTALL_URL + "/invitations/login?" + url_safe

    data = render_to_string('invitation.txt', {'email': email})

    print data

    send_async(
        frm = "%s <%s>"%(invitation.source.realname,invitation.source.uniq),
        to = "%s <%s>"%(invitation.target.realname,invitation.target.uniq),
        subject = u'Πρόσκληση για την υπηρεσία Ωκεανός',
        body = data
    )


@transaction.commit_on_success
def add_invitation(source, name, email):
    """
        Adds an invitation, if the source user has not gone over his/her
        invitation count or the target user has not been invited already
    """
    num_inv = Invitations.objects.filter(source = source).count()

    if num_inv >= settings.MAX_INVITATIONS:
        raise TooManyInvitations("User invitation limit (%d) exhausted" % settings.MAX_INVITATIONS)

    target = SynnefoUser.objects.filter(uniq = email)

    if target.count() is not 0:
        raise AlreadyInvited("User with email %s already invited" % (email))

    users.register_user(name, email)

    target = SynnefoUser.objects.filter(uniq = email)

    r = list(target[:1])
    if not r:
        raise Exception("Invited user cannot be added")

    inv = Invitations()
    inv.source = source
    inv.target = target[0]
    inv.save()
    return inv


@transaction.commit_on_success
def invitation_accepted(invitation):
    """
        Mark an invitation as accepted
    """
    invitation.accepted = True
    invitation.save()


class TooManyInvitations(Exception):
    messages = []

    def __init__(self, msg):
        self.messages.append(msg)


class AlreadyInvited(Exception):
    messages = []

    def __init__(self, msg):
        self.messages.append(msg)
