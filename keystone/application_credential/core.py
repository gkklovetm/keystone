# Copyright 2018 SUSE Linux GmbH
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

"""Main entry point into the Application Credential service."""

from oslo_log import log

from keystone.common import cache
from keystone.common import driver_hints
from keystone.common import manager
from keystone.common import provider_api
import keystone.conf
from keystone import exception
from keystone import notifications


CONF = keystone.conf.CONF
MEMOIZE = cache.get_memoization_decorator(group='application_credential')
LOG = log.getLogger(__name__)
PROVIDERS = provider_api.ProviderAPIs


class Manager(manager.Manager):
    """Default pivot point for the Application Credential backend.

    See :mod:`keystone.common.manager.Manager` for more details on how this
    dynamically calls the backend.

    """

    driver_namespace = 'keystone.application_credential'
    _provides_api = 'application_credential_api'

    _APP_CRED = 'application_credential'

    def __init__(self):
        super(Manager, self).__init__(CONF.application_credential.driver)
        self._register_callback_listeners()

    def _register_callback_listeners(self):
        notifications.register_event_callback(
            notifications.ACTIONS.deleted, 'user',
            self._delete_app_creds_on_user_delete_callback)
        notifications.register_event_callback(
            notifications.ACTIONS.disabled, 'user',
            self._delete_app_creds_on_user_delete_callback)
        notifications.register_event_callback(
            notifications.ACTIONS.internal,
            # This notification is emitted when a role assignment is removed,
            # we can take advantage of it even though we're not a token.
            notifications.INVALIDATE_USER_PROJECT_TOKEN_PERSISTENCE,
            self._delete_app_creds_on_assignment_removal)

    def _delete_app_creds_on_user_delete_callback(
            self, service, resource_type, operation, payload):
        user_id = payload['resource_info']
        self._delete_application_credentials_for_user(user_id)

    def _delete_app_creds_on_assignment_removal(
            self, service, resource_type, operation, payload):
        user_id = payload['resource_info']['user_id']
        project_id = payload['resource_info']['project_id']
        self._delete_application_credentials_for_user_on_project(user_id,
                                                                 project_id)

    def _get_user_roles(self, user_id, project_id):
        assignment_list = self.assignment_api.list_role_assignments(
            user_id=user_id,
            project_id=project_id,
            effective=True)
        return list(set([x['role_id'] for x in assignment_list]))

    def _require_user_has_role_in_project(self, roles, user_id, project_id):
        user_roles = self._get_user_roles(user_id, project_id)
        for role in roles:
            matching_roles = [x for x in user_roles
                              if x == role['id']]
            if not matching_roles:
                raise exception.RoleAssignmentNotFound(role_id=role['id'],
                                                       actor_id=user_id,
                                                       target_id=project_id)

    def _get_role_list(self, app_cred_roles):
        roles = []
        for role in app_cred_roles:
            roles.append(PROVIDERS.role_api.get_role(role['id']))
        return roles

    def authenticate(self, request, application_credential_id, secret):
        """Authenticate with an application credential.

        :param str application_credential_id: Application Credential ID
        :param str secret: Application Credential secret

        """
        self.driver.authenticate(application_credential_id, secret)

    def _process_app_cred(self, app_cred_ref):
        app_cred_ref = app_cred_ref.copy()
        app_cred_ref.pop('secret_hash')
        app_cred_ref['roles'] = self._get_role_list(
            app_cred_ref['roles'])
        return app_cred_ref

    def create_application_credential(self, application_credential,
                                      initiator=None):
        """Create a new application credential.

        :param dict application_credential: Application Credential data
        :param initiator: CADF initiator

        :returns: a new application credential
        """
        application_credential = application_credential.copy()
        user_id = application_credential['user_id']
        project_id = application_credential['project_id']
        roles = application_credential.pop('roles', [])
        self._require_user_has_role_in_project(roles, user_id, project_id)
        unhashed_secret = application_credential['secret']
        ref = self.driver.create_application_credential(
            application_credential, roles)
        ref['secret'] = unhashed_secret
        ref = self._process_app_cred(ref)
        notifications.Audit.created(
            self._APP_CRED,
            application_credential['id'],
            initiator)
        return ref

    @MEMOIZE
    def get_application_credential(self, application_credential_id):
        """Get application credential details.

        :param str application_credential_id: Application Credential ID

        :returns: an application credential
        """
        app_cred = self.driver.get_application_credential(
            application_credential_id)
        return self._process_app_cred(app_cred)

    def list_application_credentials(self, user_id, hints=None):
        """List application credentials for a user.

        :param str user_id: User ID
        :param dict hints: Properties to filter on

        :returns: a list of application credentials
        """
        hints = hints or driver_hints.Hints()
        app_cred_list = self.driver.list_application_credentials_for_user(
            user_id, hints)
        return [self._process_app_cred(app_cred) for app_cred in app_cred_list]

    def delete_application_credential(self, application_credential_id,
                                      initiator=None):
        """Delete an application credential.

        :param str application_credential_id: Application Credential ID
        :param initiator: CADF initiator

        :raises keystone.exception.ApplicationCredentialNotFound: If the
            application credential doesn't exist.
        """
        self.get_application_credential.invalidate()
        self.driver.delete_application_credential(application_credential_id)
        notifications.Audit.deleted(
            self._APP_CRED, application_credential_id, initiator)

    def _delete_application_credentials_for_user(self, user_id):
        """Delete all application credentials for a user.

        :param str user_id: User ID

        This is triggered when a user is deleted.
        """
        self.driver.delete_application_credentials_for_user(user_id)

    def _delete_application_credentials_for_user_on_project(self, user_id,
                                                            project_id):
        """Delete all application credentials for a user on a given project.

        :param str user_id: User ID
        :param str project_id: Project ID

        This is triggered when a user loses a role assignment on a project.
        """
        self.driver.delete_application_credentials_for_user_on_project(
            user_id, project_id)
