# Copyright 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Keystone External Authentication Plugins."""

import abc

import six

from keystone.auth.plugins import base
import keystone.conf
from keystone import exception
from keystone.i18n import _


CONF = keystone.conf.CONF


@six.add_metaclass(abc.ABCMeta)
class Base(base.AuthMethodHandler):
    def authenticate(self, request, auth_payload,):
        """Use REMOTE_USER to look up the user in the identity backend.

        The user_id from the actual user from the REMOTE_USER env variable is
        placed in the response_data.
        """
        response_data = {}
        if not request.remote_user:
            msg = _('No authenticated user')
            raise exception.Unauthorized(msg)

        try:
            user_ref = self._authenticate(request)
        except Exception:
            msg = _('Unable to lookup user %s') % request.remote_user
            raise exception.Unauthorized(msg)

        response_data['user_id'] = user_ref['id']
        auth_type = (request.auth_type or '').lower()

        if 'kerberos' in CONF.token.bind and auth_type == 'negotiate':
            response_data.setdefault('bind', {})['kerberos'] = user_ref['name']

        return base.AuthHandlerResponse(status=True, response_body=None,
                                        response_data=response_data)

    @abc.abstractmethod
    def _authenticate(self, request):
        """Look up the user in the identity backend.

        Return user_ref
        """
        pass


class DefaultDomain(Base):
    def _authenticate(self, request):
        """Use remote_user to look up the user in the identity backend."""
        return self.identity_api.get_user_by_name(
            request.remote_user,
            CONF.identity.default_domain_id)


class Domain(Base):
    def _authenticate(self, request):
        """Use remote_user to look up the user in the identity backend.

        The domain will be extracted from the REMOTE_DOMAIN environment
        variable if present. If not, the default domain will be used.
        """
        if request.remote_domain:
            ref = self.resource_api.get_domain_by_name(request.remote_domain)
            domain_id = ref['id']
        else:
            domain_id = CONF.identity.default_domain_id

        return self.identity_api.get_user_by_name(request.remote_user,
                                                  domain_id)


class KerberosDomain(Domain):
    """Allows `kerberos` as a method."""

    def _authenticate(self, request):
        if request.auth_type != 'Negotiate':
            raise exception.Unauthorized(_("auth_type is not Negotiate"))
        return super(KerberosDomain, self)._authenticate(request)
