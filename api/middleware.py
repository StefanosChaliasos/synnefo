from django.http import HttpResponse
from synnefo.db.models import SynnefoUser
from django.utils.cache import patch_vary_headers
import time

class ApiAuthMiddleware(object):

    auth_token = "X-Auth-Token"
    auth_user  = "X-Auth-User"
    auth_key   = "X-Auth-Key"

    def process_request(self, request):
        if not request.path.startswith('/api/') :
            return

        token = None

        # Try to find token in a cookie
        token = request.COOKIES.get('X-Auth-Token', None)

        # Try to find token in request header
        if not token:
            token = request.META.get('HTTP_X_AUTH_TOKEN', None)

        if token:
            user = None
            # Retrieve user from DB or other caching mechanism
            try:
                user = SynnefoUser.objects.get(auth_token=token)
            except SynnefoUser.DoesNotExist:
                user = None

            # Check user's auth token
            if user and (time.time() -
                time.mktime(user.auth_token_expires.timetuple())) > 0:
                # The user's token has expired, re-login
                user = None

            request.user = user
            return

        # A Rackspace API authentication request
        if self.auth_user in request.META and \
           self.auth_key in request.META and \
           'GET' == request.method:
            # This is here merely for compatibility with the Openstack API.
            # All normal users should authenticate through Shibboleth. Admin
            # users or other selected users could use this as a bypass
            # mechanism
            user = SynnefoUser.objects\
                    .filter(name = request.META[self.auth_user]) \
                    .filter(uniq = request.META[self.auth_key])

            response = HttpResponse()
            if user.count() <= 0:
                response.status_code = 401
            else:
                response.status_code = 204
                response['X-Auth-Token'] = user[0].auth_token
                # TODO: set the following fields when we do have this info
                response['X-Server-Management-Url'] = ""
                response['X-Storage-Url'] = ""
                response['X-CDN-Management-Url'] = ""
            return response

        request.user = None

    def process_response(self, request, response):
        # Tell proxies and other interested parties that the request varies
        # based on X-Auth-Token, to avoid caching of results
        patch_vary_headers(response, ('X-Auth-Token',))
        return response

