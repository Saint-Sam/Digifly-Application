"""Shared display/storage classification; never changes anatomical body IDs."""

FAMILY_PREFIXES = ('AN', 'DN', 'IN', 'MN', 'SN')


def neuron_family(neuron_type: str, *, superclass: str = '', explicit_family: str = '') -> str:
    """Retain explicit families; recognize the declared motor superclass/TTMn.

    TTMn is a motor neuron, not an unknown family because its name lacks an MN
    prefix. This exact type rule also repairs old downloads lacking superclass
    metadata. It does not equate IDs or SWCs between datasets/releases. Other
    UNKNOWN annotations stay unknown, rather than guessing their function.
    """
    explicit = explicit_family.strip().upper()
    if explicit in FAMILY_PREFIXES:
        return explicit
    if superclass.strip().casefold() == 'vnc_motor' or neuron_type.strip().casefold() == 'ttmn':
        return 'MN'
    if explicit == 'UNKNOWN':
        return 'UNKNOWN'
    return next((f for f in FAMILY_PREFIXES if neuron_type.upper().startswith(f)), 'UNKNOWN')
