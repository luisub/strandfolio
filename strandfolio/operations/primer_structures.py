"""Local model checks on the two complete DNA oligos supplied by the user."""

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from math import isfinite

from Bio import __version__ as BIOPYTHON_VERSION
from Bio.Seq import reverse_complement
from Bio.SeqUtils import MeltingTemp

from strandfolio.domain.primers import PrimerEntry


PRIMER3_VERSION = "2.4.0"
_MAX_STRUCTURE_NT = 60
_CONDITIONS = dict(mv_conc=50.0, dv_conc=1.5, dntp_conc=0.6, dna_conc=50.0, temp_c=37.0, max_loop=30)
STRUCTURE_MODEL = (
    f"primer3-py {PRIMER3_VERSION} · DNA {_CONDITIONS['dna_conc']:g} nM · "
    f"monovalent ions {_CONDITIONS['mv_conc']:g} mM · divalent ions {_CONDITIONS['dv_conc']:g} mM · "
    f"dNTPs {_CONDITIONS['dntp_conc']:g} mM · reference temperature {_CONDITIONS['temp_c']:g} °C · "
    f"maximum loop {_CONDITIONS['max_loop']} nt"
)
_NN_MODEL = "nearest-neighbor"
_LONG_MODEL = "long-oligo approximation"
DUPLEX_MODELS = (
    "Perfect-complement DNA/DNA estimates use the annealing portion only. "
    f"2–60 nt: Biopython {BIOPYTHON_VERSION} Tm_NN, SantaLucia DNA_NN3, salt correction 5. "
    "Na⁺ 50 mM, K⁺, Tris, Mg²⁺ and dNTPs 0 mM. Strand concentration is 25 nM each, or 50 nM total "
    "for a self-complementary oligo with the symmetry correction. "
    f"61–80 nt: primer3-py {PRIMER3_VERSION} long-oligo GC/length approximation, "
    "50 mM monovalent ions, no Mg²⁺, dNTPs, DMSO or formamide."
)


@dataclass(frozen=True, slots=True)
class PrimerMeltingEstimate:
    """Tm of the supplied annealing segment against its ideal complement."""

    tmC: float | None = None
    model: str = ""
    reason: str = ""

    @property
    def approximate(self) -> bool:
        return self.model == _LONG_MODEL


def estimatePrimerMelting(entry: PrimerEntry) -> PrimerMeltingEstimate:
    """Report a sequence-only duplex estimate under the editor's reference conditions."""
    annealingOnly = PrimerEntry(entry.role, entry.annealing, "", entry.modifications)
    problem = annealingOnly.problem
    if problem:
        return PrimerMeltingEstimate(reason=problem)
    bases = entry.annealing
    if not 2 <= len(bases) <= 80:
        return PrimerMeltingEstimate(reason="The duplex Tm estimate covers 2–80 annealing bases.")
    try:
        if len(bases) <= _MAX_STRUCTURE_NT:
            model = _NN_MODEL
            selfComplementary = reverse_complement(bases) == bases
            tmC = MeltingTemp.Tm_NN(
                bases, nn_table=MeltingTemp.DNA_NN3, strict=True, selfcomp=selfComplementary,
                dnac1=50 if selfComplementary else 25, dnac2=0 if selfComplementary else 25,
                Na=50, K=0, Tris=0, Mg=0, dNTPs=0, saltcorr=5,
            )
        else:
            model = _LONG_MODEL
            if version("primer3-py") != PRIMER3_VERSION:
                return PrimerMeltingEstimate(reason=f"The long-oligo estimate requires primer3-py {PRIMER3_VERSION}.")
            from primer3.thermoanalysis import ThermoAnalysis
            engine = ThermoAnalysis(mv_conc=50.0, dv_conc=0.0, dntp_conc=0.0, dna_conc=50.0,
                                    dmso_conc=0.0, formamide_conc=0.0, max_nn_length=_MAX_STRUCTURE_NT)
            tmC = engine.calc_tm(bases)
        if not isfinite(tmC):
            return PrimerMeltingEstimate(reason="The model returned a nonfinite melting temperature.")
        return PrimerMeltingEstimate(tmC, model)
    except (ImportError, OSError, RuntimeError, ValueError, ArithmeticError) as error:
        return PrimerMeltingEstimate(reason=f"Duplex Tm unavailable. {error}")


@dataclass(frozen=True, slots=True)
class PrimerStructureResult:
    """A structure check with predicted set to True, False or None if it could not be assessed."""

    name: str
    predicted: bool | None
    tmC: float | None = None
    reason: str = ""


def assessPrimerStructures(forward: PrimerEntry, reverse: PrimerEntry) -> tuple[PrimerStructureResult, ...]:
    """Check complete oligos in input orientation, without reading a template or changing either input."""
    checks = (
        ("Forward hairpin", "calc_hairpin", (forward,)),
        ("Forward self-dimer", "calc_homodimer", (forward,)),
        ("Reverse hairpin", "calc_hairpin", (reverse,)),
        ("Reverse self-dimer", "calc_homodimer", (reverse,)),
        ("Forward–reverse dimer", "calc_heterodimer", (forward, reverse)),
    )
    try:
        installed = version("primer3-py")
        if installed != PRIMER3_VERSION:
            raise ValueError(f"This panel requires primer3-py {PRIMER3_VERSION}. Found {installed}.")
        from primer3.thermoanalysis import ThermoAnalysis
        engine = ThermoAnalysis(**_CONDITIONS)
    except (PackageNotFoundError, ImportError, OSError, RuntimeError, ValueError) as error:
        return tuple(PrimerStructureResult(name, None, reason=f"Structure engine unavailable. {error}")
                     for name, _, _ in checks)

    results = []
    for name, method, entries in checks:
        reason = ""
        for entry in entries:
            reason = entry.problem
            if not reason and len(entry.sequence) > _MAX_STRUCTURE_NT:
                reason = f"The structure model covers complete oligos up to {_MAX_STRUCTURE_NT} nt including tails."
            if reason:
                break
        if reason:
            results.append(PrimerStructureResult(name, None, reason=reason))
            continue
        try:
            result = getattr(engine, method)(*(entry.sequence for entry in entries))
            predicted = result.structure_found
            tmC = result.tm if predicted else None
            if tmC is not None and not isfinite(tmC):
                raise ValueError("The model returned a nonfinite melting temperature.")
            results.append(PrimerStructureResult(name, predicted, tmC))
        except (OSError, ValueError, RuntimeError, OverflowError) as error:
            results.append(PrimerStructureResult(name, None, reason=f"Calculation unavailable. {error}"))
    return tuple(results)
