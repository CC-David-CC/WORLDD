"""Exact broker-request trace contracts for native actor gateway regressions."""

FINAL_WINDOW_SEQUENCE = (
    'operation_complete',
    'projection_workspace_access',
    'projection_binding',
    'projection_browse_denied',
    'projection_read_denied',
    'projection_create_allowed',
    'projection_binding_confirmation',
    'projection_workspace_access_render_before',
    'projection_workspace_access_render_after',
    'projection_observation_fence',
    'post_observe_binding',
    'final_create_check',
    'final_binding_confirmation',
    'final_authorization_fence',
)

def scoped_check_matches(check, workspace, principal, right, components):
    """Match the entire workspace-authority tuple, including its scope."""
    return (check.workspace == workspace and check.principal == principal and
            check.right == right and tuple(check.components) == tuple(components))


def classify_gateway_request(request, reply=None):
    """Classify actual broker protobuf traffic and validate its reply oneof."""
    if request.HasField('scoped_authority_check'):
        if reply is not None and not reply.HasField('scoped_authority_check'):
            raise AssertionError(f'scoped authority reply mismatch: {reply!r}')
        check = request.scoped_authority_check
        return ('scoped_authority_check', check.workspace, check.principal.SerializeToString(),
                check.right, tuple(check.components))
    if request.HasField('authorization_fence'):
        if reply is not None and not reply.HasField('authorization_fence'):
            raise AssertionError(f'authorization fence reply mismatch: {reply!r}')
        return ('authorization_fence', request.authorization_fence.principal.SerializeToString())
    if request.HasField('management') and request.management.HasField('workspace_get'):
        if reply is not None and (not reply.HasField('management') or
                                  not reply.management.HasField('workspace')):
            raise AssertionError(f'workspace binding reply mismatch: {reply!r}')
        return ('workspace_get', request.management.workspace_get.id)
    if request.HasField('file_action'):
        if reply is not None and not reply.HasField('file_action'):
            raise AssertionError(f'file action reply mismatch: {reply!r}')
        action = request.file_action
        kind = action.WhichOneof('action')
        return ('file_action', action.workspace_id, action.operation_id,
                action.principal.SerializeToString(), kind)
    return ('other', request.WhichOneof('operation'))


def record_file_action_attempt(request, counters):
    """Count every attempted broker file action before phase or identity filters."""
    if request.HasField('file_action'):
        counters['file_action_attempts'] += 1
        return classify_gateway_request(request)
    return None


def reject_terminal_gateway_request(request):
    """Reject repeated gateway traffic while a selected actor action is pending."""
    category = classify_gateway_request(request)
    if category[0] != 'other':
        raise AssertionError(f'unexpected gateway request after terminal fence: {category!r}')
    return category


def require_create_payload(request, *, basename, content, directory_handle=b''):
    """Read Python protobuf message properties and verify the dispatched payload."""
    if not request.HasField('file_action') or not request.file_action.HasField('create'):
        raise AssertionError('file-create payload required')
    action = request.file_action.create
    if (action.directory_handle != directory_handle or action.basename != basename or
            action.content != content):
        raise AssertionError('selected file-create payload changed')
    return action


class FinalWindowBrokerTrace:
    """Validate live request/reply phases before enabling the final-fence barrier."""

    def __init__(self, *, workspace, principal, worktree, operation, basename, content,
                 workspace_access, browse, read, create):
        self.workspace = workspace
        self.principal = principal.SerializeToString()
        self.worktree = worktree
        self.operation = operation
        self.basename = basename
        self.content = content
        self.rights = (workspace_access, browse, read, create)
        self.events = []
        self.binding = None
        self.projection_stamps = []
        self.final_stamp = None

    @property
    def stage(self):
        return len(self.events)

    @property
    def final_fence_reached(self):
        return self.stage == len(FINAL_WINDOW_SEQUENCE)

    def finish(self):
        if not self.final_fence_reached:
            raise AssertionError(f'incomplete final-window requests: {self.events!r}')

    def _expected_request(self):
        access, browse, read, create = self.rights
        if self.stage == 0:
            return ('file_action', self.workspace, self.operation, self.principal, 'create')
        if self.stage in (2, 6, 10, 12):
            return ('workspace_get', self.workspace)
        if self.stage in (9, 13):
            return ('authorization_fence', self.principal)
        rights = {1: access, 3: browse, 4: read, 5: create, 7: access, 8: access, 11: create}
        right = rights[self.stage]
        components = () if self.stage in (1, 7, 8) else (self.worktree,)
        return ('scoped_authority_check', self.workspace, self.principal, right, components)

    def exchange(self, request, exchange):
        """Use the same transition path for native hooks and transcript controls."""
        if self.final_fence_reached:
            reject_terminal_gateway_request(request)
            return exchange(request)
        expected = self._expected_request()
        category = classify_gateway_request(request)
        if category != expected:
            raise AssertionError(f'final-window phase {self.stage}: got {category!r}; expected {expected!r}')
        if self.stage == 0:
            require_create_payload(request, basename=self.basename, content=self.content)
        response = exchange(request)
        classify_gateway_request(request, response)
        if category[0] == 'file_action':
            result = response.file_action
            if not result.HasField('object') or result.operation_id != self.operation:
                raise AssertionError(f'selected file operation did not complete: {response!r}')
        elif category[0] == 'workspace_get':
            binding = response.management.workspace
            current = (binding.id, binding.worktree.encode('utf-8'), binding.revision)
            if current[:2] != (self.workspace, self.worktree):
                raise AssertionError(f'workspace binding identity changed: {current!r}')
            if self.binding is not None and current != self.binding:
                raise AssertionError(f'workspace binding revision changed: {current!r}')
            self.binding = current
        elif category[0] == 'scoped_authority_check':
            decision = response.scoped_authority_check
            expected_allowed = self.stage in (5, 11)
            if decision.allowed != expected_allowed:
                raise AssertionError(f'wrong authority decision at phase {self.stage}: {decision!r}')
            stamp = self._stamp(decision)
            if self.stage == 11:
                self.final_stamp = stamp
            else:
                self.projection_stamps.append(stamp)
        else:
            stamp = self._stamp(response.authorization_fence)
            if self.stage == 9:
                if not self.projection_stamps or any(value != stamp for value in self.projection_stamps):
                    raise AssertionError('observation fence does not match projection decisions')
            elif self.final_stamp is None or stamp != self.final_stamp:
                raise AssertionError('final fence does not match operation-specific decision')
        self.events.append(FINAL_WINDOW_SEQUENCE[self.stage])
        return response

    @staticmethod
    def _stamp(value):
        if len(value.incarnation) != 16 or value.epoch <= 0:
            raise AssertionError('invalid authority incarnation/epoch')
        return (value.incarnation, value.epoch)
