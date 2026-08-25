"""FROZEN CONTRACT. Spec Part 8 -- a missing runner is never a pass."""

from nokinc_factory.ports.toolchain import REFERENCE, GateName


def test_hcl_has_no_unit_runner() -> None:
    """Terraform has no unit test concept. That must surface as NOT_AVAILABLE."""
    assert not REFERENCE["hcl"].supports(GateName.UNIT)


def test_every_reference_toolchain_supports_build_and_types() -> None:
    for name, spec in REFERENCE.items():
        assert spec.supports(GateName.BUILD), name
        assert spec.supports(GateName.TYPES), name


def test_gate_names_are_shared_across_languages() -> None:
    """The point of the port: one gate vocabulary, many implementations."""
    py, ts = REFERENCE["python"], REFERENCE["typescript"]
    shared = set(py.commands) & set(ts.commands)
    assert {GateName.BUILD, GateName.UNIT, GateName.TYPES} <= shared
    assert py.commands[GateName.UNIT] != ts.commands[GateName.UNIT]
