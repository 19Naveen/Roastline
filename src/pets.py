"""Small, static ASCII companions for an optional Roastline decoration."""

PETS = {
    "cat": "(=^.^=)",
    "dog": "(U^.^U)",
    "anime": "(^_^)",
    "person": "(:-)",
}


def decorate(roast, pet):
    """Prefix a nonempty roast with a pet; unknown selections leave it alone.

    Faces and their separator are printable ASCII, so their display width is
    their length. The caller remains responsible for any overall width limit.
    """
    face = PETS.get(pet) if isinstance(pet, str) else None
    return f"{face} {roast}" if face and roast else roast
