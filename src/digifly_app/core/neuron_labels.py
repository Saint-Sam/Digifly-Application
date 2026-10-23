"""Shared display/storage classification; never changes anatomical body IDs."""

FAMILY_PREFIXES = ('AN', 'DN', 'IN', 'MN', 'SN')


def canonical_saved_type(value: str, dataset: str, server: str = 'https://neuprint.janelia.org') -> str:
    """Reconcile only documented type/instance aliases, not arbitrary conflicts.

    The user-supplied neuPrint MANC1.2.1 table identifies MNwm34_PDMNp_L/R
    instances as type TTMn; Male1.0 uses TTMn_L/R. Old exports sometimes used
    those instance names as type folders. Keep the originals alongside this
    display name. No body ID or morphology is mapped between connectomes.
    """
    if server.rstrip('/') != 'https://neuprint.janelia.org':
        return value
    name = value.strip().casefold()
    if name == 'ttmn':
        return 'TTMn'
    if dataset in {'manc:v1.2.1', 'manc_v1.2.1'} and name in {'mnwm34_pdmnp_l', 'mnwm34_pdmnp_r'}:
        return 'TTMn'
    if dataset in {'male-cns:v1.0', 'male-cns_v1.0'} and name in {'ttmn_l', 'ttmn_r'}:
        return 'TTMn'
    return value


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
