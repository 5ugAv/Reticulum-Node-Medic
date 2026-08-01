"""The recovery key — format, folding, and the three-strikes rule."""

from provisioning import recovery_key as rk


def test_generated_key_is_wellformed_and_grouped():
    k = rk.generate()
    assert rk.is_wellformed(k)
    assert k.count("-") == rk.GROUPS - 1
    assert len(rk.normalize(k)) == rk.GROUP_LEN * rk.GROUPS
    assert rk.entropy_bits() == 160


def test_keys_are_unique():
    keys = {rk.generate() for _ in range(50)}
    assert len(keys) == 50


def test_alphabet_excludes_confusable_letters():
    # a handwritten key must not be ambiguous: no I/L (vs 1), O (vs 0), U (vs V)
    for bad in "ILOU":
        assert bad not in rk.ALPHABET


def test_handwriting_confusions_are_folded_on_input():
    k = rk.generate()
    bare = rk.normalize(k)
    typed = bare.replace("0", "O").replace("1", "I").lower()
    assert rk.normalize(typed) == bare


def test_separators_and_spaces_are_ignored():
    k = rk.generate()
    bare = rk.normalize(k)
    assert rk.normalize(k.replace("-", " ")) == bare
    assert rk.normalize(k.replace("-", "")) == bare
    assert rk.normalize(f"  {k}\n") == bare


def test_wrong_length_or_alphabet_is_rejected():
    assert not rk.is_wellformed("")
    assert not rk.is_wellformed("ABCD-EFGH")                  # too short
    assert not rk.is_wellformed(rk.generate() + "-ZZZZ")      # too long
    assert not rk.is_wellformed("!" * (rk.GROUP_LEN * rk.GROUPS))


def test_recovery_is_offered_only_after_three_failures():
    assert not rk.should_offer_recovery(0)
    assert not rk.should_offer_recovery(2)
    assert rk.should_offer_recovery(3)
    assert rk.should_offer_recovery(9)


def test_groups_split_for_display():
    k = rk.generate()
    gs = rk.groups(k)
    assert len(gs) == rk.GROUPS
    assert all(len(g) == rk.GROUP_LEN for g in gs)
    assert "".join(gs) == rk.normalize(k)


def test_records_only_vault_leaves_the_mesh_identity_outside():
    """The operator's shape (2026-08-02): the medic must rejoin the mesh by
    itself after a power cut, so ~/.reticulum and ~/.lxmd stay UNencrypted."""
    from provisioning.vault import RECORDS_ROOTS
    assert RECORDS_ROOTS == (".reticulum-node-medic",)
    assert ".reticulum" not in RECORDS_ROOTS
    assert ".lxmd" not in RECORDS_ROOTS
