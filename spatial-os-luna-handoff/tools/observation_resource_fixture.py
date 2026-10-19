"""Focused resource funding for the application observation fixture."""


def fund_actor_application_reservations(resource_config, workspace_id, *, applications):
    """Fund bounded same-actor application reservations in the selected policy."""
    if (not isinstance(applications, int) or isinstance(applications, bool) or
            not 1 <= applications <= 128):
        raise ValueError('Application reservation count is outside the supported range')
    if not workspace_id:
        raise ValueError('Observation fixture requires a workspace identity')
    if not resource_config.HasField('broker_budget') or not resource_config.HasField('manager_budget'):
        raise ValueError('Broker and manager envelopes are required for fixture funding')
    if not resource_config.HasField('default_policy'):
        raise ValueError('Default resource policy is required for fixture funding')

    selected = [policy for policy in resource_config.workspaces
                if policy.workspace == workspace_id]
    if len(selected) > 1:
        raise ValueError('Duplicate explicit workspace resource policy')
    policy = selected[0] if selected else resource_config.default_policy
    if not policy.HasField('total') or not policy.HasField('actor_default') or not policy.profiles:
        raise ValueError('Selected resource policy is incomplete')
    maxima = []
    for profile in policy.profiles:
        if not profile.HasField('maximum'):
            raise ValueError('Selected application profile is incomplete')
        maximum = profile.maximum
        if not maximum.HasField('memory_bytes') or not maximum.HasField('swap_bytes') or not maximum.HasField('processes'):
            raise ValueError('Selected application profile lacks a reservation dimension')
        maxima.append((maximum.memory_bytes, maximum.swap_bytes, maximum.processes))
    required = tuple(applications * max(values[index] for values in maxima) for index in range(3))
    dimensions = ('memory_bytes', 'swap_bytes', 'processes')
    for dimension, amount in zip(dimensions, required):
        if not policy.total.HasField(dimension) or getattr(policy.total, dimension) < amount:
            raise ValueError('Selected workspace resource ceiling cannot fund application reservations')
    broker = resource_config.broker_budget
    manager = resource_config.manager_budget
    for dimension, amount in zip(dimensions, required):
        if not broker.HasField(dimension) or not manager.HasField(dimension):
            raise ValueError('Broker envelope lacks a reservation dimension')
        if getattr(manager, dimension) > getattr(broker, dimension) or amount > getattr(broker, dimension) - getattr(manager, dimension):
            raise ValueError('Broker resource headroom cannot fund application reservations')
    actor = policy.actor_default
    if not actor.HasField('swap_bytes') or actor.swap_bytes < required[1]:
        raise ValueError('Selected actor swap budget cannot fund application reservations')
    if not actor.HasField('memory_bytes') or not actor.HasField('processes'):
        raise ValueError('Selected actor budget lacks a fundable dimension')

    actor.memory_bytes = max(actor.memory_bytes, required[0])
    actor.processes = max(actor.processes, required[2])
    return dict(policy_workspace=policy.workspace, applications=applications,
                reserved_memory_bytes=required[0], reserved_swap_bytes=required[1],
                reserved_processes=required[2])
