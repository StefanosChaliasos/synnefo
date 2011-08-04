# Copyright 2011 GRNET S.A. All rights reserved.
# 
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
# 
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
# 
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
# 
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

from time import time, mktime

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.cache import patch_vary_headers

from models import PithosUser
from shibboleth import Tokens, register_shibboleth_user


def login(request):
    return HttpResponse('login')

#     # Special case for testing purposes, delivers the cookie for the
#     # test user on first access
#     if settings.BYPASS_AUTHENTICATION and \
#        request.GET.get('test') is not None:
#         u = PithosUser.objects.get(
#             auth_token='46e427d657b20defe352804f0eb6f8a2')
#         return _redirect_shib_auth_user(user = u)
# 
#     token = None
# 
#     # Try to find token in a cookie
#     token = request.COOKIES.get('X-Auth-Token', None)
# 
#     # Try to find token in request header
#     if not token:
#         token = request.META.get('HTTP_X_AUTH_TOKEN', None)
# 
#     if token:
#         # token was found, retrieve user from backing store
#         try:
#             user = PithosUser.objects.get(auth_token=token)
# 
#         except PithosUser.DoesNotExist:
#             return HttpResponseRedirect(settings.LOGIN_URL)
#         # check user's auth token validity
#         if (time() - mktime(user.auth_token_expires.timetuple())) > 0:
#             # the user's token has expired, prompt to re-login
#             return HttpResponseRedirect(settings.LOGIN_URL)
# 
#         request.user = user
#         return
# 
#     # token was not found but user authenticated by Shibboleth
#     if Tokens.SHIB_EPPN in request.META and \
#        Tokens.SHIB_SESSION_ID in request.META:
#         try:
#             user = PithosUser.objects.get(uniq=request.META[Tokens.SHIB_EPPN])
#             return _redirect_shib_auth_user(user)
#         except PithosUser.DoesNotExist:
#             if register_shibboleth_user(request.META):
#                 user = PithosUser.objects.get(uniq=request.META[Tokens.SHIB_EPPN])
#                 return _redirect_shib_auth_user(user)
#             else:
#                 return HttpResponseRedirect(settings.LOGIN_URL)
# 
#     if settings.TEST and 'TEST-AAI' in request.META:
#         return HttpResponseRedirect(settings.LOGIN_URL)
# 
#     if request.path.endswith(settings.LOGIN_URL):
#         # avoid redirect loops
#         return
#     else:
#         # no authentication info found in headers, redirect back
#         return HttpResponseRedirect(settings.LOGIN_URL)
# 
# def process_response(request, response):
#     # Tell proxies and other interested parties that the request varies
#     # based on X-Auth-Token, to avoid caching of results
#     patch_vary_headers(response, ('X-Auth-Token',))
#     return response
# 
# def _redirect_shib_auth_user(user):
#     expire_fmt = user.auth_token_expires.strftime('%a, %d-%b-%Y %H:%M:%S %Z')
# 
#     response = HttpResponse()
#     response.set_cookie('X-Auth-Token', value=user.auth_token,
#                         expires=expire_fmt, path='/')
#     response['X-Auth-Token'] = user.auth_token
#     response['Location'] = settings.APP_INSTALL_URL
#     response.status_code = 302
#     return response
