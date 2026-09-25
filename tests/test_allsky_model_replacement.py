"""Tests for services.allsky.model_replacement — does an admitted automatic
fit replace the incumbent?

The basin-escape rules are the point: an escape result bypasses the RMS
guard only when it was admitted on evidence. Without evidence (an
uncorroborated incumbent — every pre-provenance installation — and no
trusted pole) it is held to the normal comparison, so a wrong-scale
bootstrap over a hazy buffer cannot overwrite a working model, while a
genuinely better candidate can still displace a wrong cold-start one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.calibration_quality import model_quality
from services.allsky.fisheye import FisheyeModel
from services.allsky.model_replacement import (
    COMPARABLE_MIN_IMAGES, COMPARABLE_MIN_MATCHES, ESCAPE_MIN_RMS_GAIN,
    RMS_REGRESSION_TOLERANCE, _rms_is_comparable, should_replace)


def _m(rms, n_matches, n_images=20, span=60.0, **over):
    m = FisheyeModel(cx=960.0, cy=540.0, a1=600.0, rms_residual=rms,
                     n_matches=n_matches, n_images=n_images, span_minutes=span)
    for k, v in over.items():
        setattr(m, k, v)
    return m


def _q(m):
    return model_quality(m, m.n_images, m.span_minutes)


def _decide(inc, cand, **kw):
    return should_replace(inc, _q(inc), cand, _q(cand), **kw)


class TestNoIncumbent:
    def test_anything_replaces_nothing(self):
        ok, why = should_replace(None, 'none', _m(15.0, 10), 'preliminary')
        assert ok and 'no incumbent' in why


class TestBasinEscape:
    # The #10 shape: a working excellent model vs a wrong-scale escape with
    # fewer matches and a plausible RMS.
    WORKING = dict(rms=7.7, n_matches=4561, n_images=40, span=80.0)
    WRONG_SCALE = dict(rms=6.0, n_matches=900, n_images=20, span=60.0)

    def test_escape_on_evidence_bypasses_rms_guard(self):
        flattering = _m(4.0, 11, n_images=1, span=0.0)
        honest = _m(9.0, 200)
        ok, why = _decide(flattering, honest, escape=True, evidence=True)
        assert ok and 'without the RMS guard' in why

    def test_escape_without_evidence_cannot_demote_working_model(self):
        """The review blocker, as the decision sees it."""
        ok, why = _decide(_m(**self.WORKING), _m(**self.WRONG_SCALE),
                          escape=True, evidence=False)
        assert not ok
        assert 'without evidence' in why and 'normal comparison' in why

    def test_same_candidate_wins_on_evidence(self):
        """Same numbers, admitted against a trusted pole or an authoritative
        incumbent: the escape premise holds and the RMS guard is waived."""
        ok, _ = _decide(_m(**self.WORKING), _m(**self.WRONG_SCALE),
                        escape=True, evidence=True)
        assert ok

    def test_escape_without_evidence_still_wins_when_better(self):
        """Blocker-1 regression guard: no permanent lockout on no evidence."""
        wrong_cold_start = _m(9.0, 300)
        truth = _m(7.0, 1200, n_images=25, span=70.0)
        ok, _ = _decide(wrong_cold_start, truth, escape=True, evidence=False)
        assert ok

    def test_escape_without_evidence_respects_rms_guard(self):
        ok, why = _decide(_m(4.0, 100), _m(9.0, 200), escape=True, evidence=False)
        assert not ok and 'worse' in why

    def test_evidence_without_escape_changes_nothing(self):
        """Evidence only waives the guard for an escape; a seeded refinement
        admitted on evidence is still held to the normal comparison."""
        ok, _ = _decide(_m(4.0, 100), _m(9.0, 200), escape=False, evidence=True)
        assert not ok


class TestGuidedSingleSolve:
    def _guided(self):
        return _m(2.4, 7, n_images=1, span=0.0, provenance='guided')

    def test_multi_image_candidate_replaces_on_rank(self):
        ok, why = _decide(self._guided(), _m(7.7, 4561, n_images=40, span=80.0))
        assert ok and 'guided single solve' in why

    def test_single_image_candidate_does_not(self):
        ok, _ = _decide(self._guided(), _m(6.0, 40, n_images=1, span=0.0))
        assert not ok

    def test_multi_image_guided_incumbent_uses_normal_guard(self):
        inc = _m(7.7, 4561, n_images=40, span=80.0, provenance='guided')
        assert not _decide(inc, _m(12.0, 5000, n_images=45, span=90.0))[0]


class TestRmsGuard:
    def test_tolerance(self):
        assert RMS_REGRESSION_TOLERANCE == pytest.approx(1.15)

    def test_rank_upgrade_within_bound(self):
        inc = _m(10.0, 50, n_images=3, span=30.0)
        cand = _m(11.0, 110, n_images=12, span=40.0)
        ok, why = _decide(inc, cand)
        assert ok and 'rank upgrade' in why

    def test_rank_upgrade_beyond_bound_rejected(self):
        inc = _m(3.0, 100, n_images=3, span=30.0)
        assert not _decide(inc, _m(4.0, 120, n_images=12, span=40.0))[0]

    def test_strict_improvement(self):
        ok, why = _decide(_m(8.0, 80), _m(6.0, 85))
        assert ok and 'lower RMS' in why

    def test_fewer_matches_is_not_an_improvement(self):
        ok, why = _decide(_m(8.0, 80), _m(6.0, 60))
        assert not ok and 'not better' in why


class TestEscapeMaterialGain:
    """2026-09-05: an evidence-free escape (axis_alt 84° → 67°, centre moved
    ~98 px) was installed over a correct model on RMS 8.03 vs 8.04 px with
    828 vs 767 matches. Noise between two fits of one lens must not pick a
    basin."""
    INCUMBENT = dict(rms=8.04, n_matches=767, n_images=40, span=80.0)
    ESCAPE = dict(rms=8.03, n_matches=828, n_images=60, span=47.0)

    def test_the_incident_swap_is_refused(self):
        ok, why = _decide(_m(**self.INCUMBENT), _m(**self.ESCAPE),
                          escape=True, evidence=False)
        assert not ok
        assert 'not a material improvement' in why

    def test_same_numbers_as_a_seeded_refinement_still_win(self):
        """The floor is for escapes only: a seeded refinement of the same
        basin may improve by a hair."""
        ok, _ = _decide(_m(**self.INCUMBENT), _m(**self.ESCAPE),
                        escape=False, evidence=False)
        assert ok

    def test_same_numbers_on_evidence_still_win(self):
        ok, _ = _decide(_m(**self.INCUMBENT), _m(**self.ESCAPE),
                        escape=True, evidence=True)
        assert ok

    def test_floor_boundary(self):
        inc = _m(10.0, 500)
        ok, _ = _decide(inc, _m(10.0 * ESCAPE_MIN_RMS_GAIN, 500),
                        escape=True, evidence=False)
        assert ok
        ok, _ = _decide(inc, _m(10.0 * ESCAPE_MIN_RMS_GAIN + 0.01, 500),
                        escape=True, evidence=False)
        assert not ok

    def test_rank_upgrade_alone_does_not_carry_an_escape(self):
        weak = _m(8.0, 300, n_images=3, span=6.0)        # low rank
        strong_rank = _m(7.9, 900, n_images=60, span=90.0)  # high rank, tiny gain
        assert _decide(weak, strong_rank, escape=False)[0]
        assert not _decide(weak, strong_rank, escape=True, evidence=False)[0]

    def test_fewer_matches_never_win_an_escape(self):
        ok, _ = _decide(_m(10.0, 1000), _m(5.0, 400), escape=True, evidence=False)
        assert not ok


class TestEscapeOverAnUnhealthyIncumbent:
    """#33, 19:58:17 on the reporter's rig: the model on disk is a single
    image fitted to five matched stars. It misses the bright anchors on every
    recent frame — the only reason an escape runs at all — yet its 2.43 px
    turned away a 726-match joint fit that passed that same anchor check, 41
    times in one night."""
    DISK = dict(rms=2.43, n_matches=5, n_images=1, span=0.0)
    ESCAPE = dict(rms=10.96, n_matches=726, n_images=53, span=90.0)

    def test_the_reported_escape_lands(self):
        ok, why = _decide(_m(**self.DISK), _m(**self.ESCAPE),
                          escape=True, evidence=False,
                          incumbent_failed_anchors=True)
        assert ok and 'bright-anchor check' in why

    def test_it_lands_over_a_comparable_incumbent_too(self):
        """Rule 3 on its own: an anchor-failing incumbent gets no RMS veto
        even when its RMS is over enough data to mean something."""
        healthy_looking = _m(2.43, 100, n_images=5, span=30.0)
        ok, why = _decide(healthy_looking, _m(**self.ESCAPE),
                          escape=True, evidence=False,
                          incumbent_failed_anchors=True)
        assert ok and 'does not get an RMS veto' in why

    def test_a_comparable_incumbent_that_passed_keeps_its_veto(self):
        """Health True never reaches here (the service cancels the escape),
        and health None passes False — either way the guard is unchanged."""
        healthy_looking = _m(2.43, 100, n_images=5, span=30.0)
        ok, why = _decide(healthy_looking, _m(**self.ESCAPE),
                          escape=True, evidence=False,
                          incumbent_failed_anchors=False)
        assert not ok and 'worse' in why

    def test_unknown_health_is_the_default(self):
        """The service passes False for an obstructed buffer, which must be
        exactly the pre-#33 call."""
        args = (_m(2.43, 100, n_images=5, span=30.0), _m(**self.ESCAPE))
        assert (_decide(*args, escape=True, evidence=False,
                        incumbent_failed_anchors=False)
                == _decide(*args, escape=True, evidence=False))

    def test_the_flag_does_nothing_without_an_escape(self):
        ok, _ = _decide(_m(2.43, 100, n_images=5, span=30.0), _m(**self.ESCAPE),
                        incumbent_failed_anchors=True)
        assert not ok


class TestIncomparableIncumbentRms:
    """Fix 2: 8 lens parameters fitted to 5 matched stars leave 2 residual
    degrees of freedom. The RMS such a solve reports is interpolation, and it
    must not veto a fit over hundreds of matches."""

    def test_the_five_match_incumbent_loses_on_rank(self):
        ok, why = _decide(_m(2.43, 5, n_images=1, span=0.0),
                          _m(10.96, 726, n_images=53, span=90.0),
                          escape=True, evidence=False)
        assert ok
        assert 'not comparable' in why and 'rank upgrade' in why

    def test_a_seeded_refinement_wins_the_same_way(self):
        ok, why = _decide(_m(2.43, 5, n_images=1, span=0.0),
                          _m(10.96, 726, n_images=53, span=90.0))
        assert ok and 'not comparable' in why

    def test_no_rank_upgrade_still_loses(self):
        # 8 matches, not 5: calibration_quality's MIN_AUTO_MATCHES floor (fix 3)
        # rates anything thinner 'none', which would make the candidate a rank
        # upgrade and decide the case before the no-upgrade path is reached.
        ok, why = _decide(_m(2.43, 8, n_images=1, span=0.0),
                          _m(10.96, 20, n_images=1, span=0.0))
        assert not ok and 'not comparable' in why

    def test_enough_matches_alone_makes_it_comparable(self):
        inc = _m(2.43, COMPARABLE_MIN_MATCHES, n_images=1, span=0.0)
        ok, why = _decide(inc, _m(10.96, 726, n_images=53, span=90.0))
        assert not ok and 'worse' in why

    def test_enough_images_alone_makes_it_comparable(self):
        inc = _m(2.43, 5, n_images=COMPARABLE_MIN_IMAGES, span=10.0)
        ok, why = _decide(inc, _m(10.96, 726, n_images=53, span=90.0))
        assert not ok and 'worse' in why

    def test_the_2026_09_05_incumbent_stays_comparable(self):
        """767 matches over 40 images: the material-gain floor still binds on
        the incident numbers, so fix 2 cannot reopen it."""
        inc = _m(**TestEscapeMaterialGain.INCUMBENT)
        assert _rms_is_comparable(inc)
        ok, why = _decide(inc, _m(**TestEscapeMaterialGain.ESCAPE),
                          escape=True, evidence=False)
        assert not ok and 'not a material improvement' in why


class TestGuidedIncumbentIsExemptFromTheAnchorRule:
    """Review blocker 1: rule 3 (basin escape over an anchor-failing
    incumbent) applies no rank and no RMS check whatsoever, and it used to be
    evaluated before the guided branch — so a 19 px 'preliminary' fit over 40
    matches replaced a human-anchored 2.40 px solve. A guided model marks a
    basin a person anchored; it outranks even the measured pole
    (.claude/rules/allsky.md), and the anchor gate is the weakest thing that
    could speak against it. Rule 3 now skips it and rule 4 (rank only)
    decides."""
    GUIDED = dict(rms=2.40, n_matches=9, n_images=1, span=0.0,
                  provenance='guided')
    ESCAPE = dict(rms=19.0, n_matches=40, n_images=3, span=20.0)

    def test_the_reported_swap_is_refused(self):
        ok, why = should_replace(_m(**self.GUIDED), 'good', _m(**self.ESCAPE),
                                 'preliminary', escape=True, evidence=False,
                                 incumbent_failed_anchors=True)
        assert not ok and 'guided single solve' in why

    def test_the_same_numbers_replace_an_unguided_incumbent(self):
        """Nothing else about the case changed — only who drew the basin."""
        unguided = {k: v for k, v in self.GUIDED.items() if k != 'provenance'}
        ok, why = should_replace(_m(**unguided), 'good', _m(**self.ESCAPE),
                                 'preliminary', escape=True, evidence=False,
                                 incumbent_failed_anchors=True)
        assert ok and 'does not get an RMS veto' in why

    def test_a_better_ranked_joint_fit_still_supersedes_the_guided_solve(self):
        """Rule 4 is not a lock: an anchor-failing guided single solve still
        loses to a candidate that is genuinely better on rank."""
        ok, why = _decide(_m(**self.GUIDED),
                          _m(7.7, 4561, n_images=40, span=80.0),
                          escape=True, evidence=False,
                          incumbent_failed_anchors=True)
        assert ok and 'guided single solve' in why

    def test_an_escape_admitted_on_evidence_is_unaffected(self):
        """Rule 2 sits above rule 3 and is untouched — model_admission has
        already weighed the guided model by the time evidence is True."""
        ok, _ = should_replace(_m(**self.GUIDED), 'good', _m(**self.ESCAPE),
                               'preliminary', escape=True, evidence=True,
                               incumbent_failed_anchors=True)
        assert ok


class TestAnchorFailureOutranksThe2026_09_05_Floor:
    """Not a replay of 2026-09-05. That night's incumbent PASSED the anchor
    gate, so the escape was cancelled before any candidate existed and the
    material-gain floor below still refuses these numbers with the flag off.
    With the flag ON the same numbers describe a different night: a
    767-match model that definitely misses the bright stars on all three
    recent frames while the candidate hits them. That is real evidence, not
    a 0.01 px coin flip, and rule 3 replaces without an RMS comparison. The
    guard against the 2026-09-05 case lives in incumbent_anchor_health being
    strict about ever saying False (.claude/rules/allsky.md)."""

    # Review blocker 2: incumbent_anchor_health returns False only when the
    # whole recent window was testable and a majority failed. That night's
    # incumbent PASSED the gate (the escape was cancelled before any
    # candidate existed), and a cloudy 1-pass/1-fail buffer now reports None,
    # which arrives here as incumbent_failed_anchors=False.
    def test_the_incident_numbers_replace_only_on_a_definite_anchor_failure(self):
        args = (_m(**TestEscapeMaterialGain.INCUMBENT),
                _m(**TestEscapeMaterialGain.ESCAPE))
        ok, why = _decide(*args, escape=True, evidence=False,
                          incumbent_failed_anchors=True)
        assert ok and 'does not get an RMS veto' in why

        ok, why = _decide(*args, escape=True, evidence=False,
                          incumbent_failed_anchors=False)
        assert not ok and 'not a material improvement' in why


class TestEscapeBypassDemandsCandidateMerit:
    """#93: rule 2's evidence bypass installed a fit that was 1.0x chance
    because 'evidence' meant only that a pole existed — and that pole was a
    light on the pier. The bypass now also needs the candidate's own
    RMS-to-tolerance and chance ratios to pass calibration_fit_merit."""

    WORKING = dict(rms=7.7, n_matches=4561, n_images=40, span=80.0)
    # The reporter's saved model as a candidate: 606 matches, RMS 10.9 px on
    # a 16.5 px final tolerance, 1.04x chance.
    CHANCE_FIT = dict(rms=10.9, n_matches=606, n_images=60, span=62.6,
                      final_tol_px=16.5, chance_ratio=1.04)

    def test_a_chance_fit_does_not_get_the_evidence_bypass(self):
        ok, why = _decide(_m(**self.WORKING), _m(**self.CHANCE_FIT),
                          escape=True, evidence=True)
        assert not ok
        assert 'no merit of its own' in why and 'chance' in why

    def test_the_same_candidate_with_credible_numbers_still_does(self):
        cand = _m(**dict(self.CHANCE_FIT, rms=7.0, chance_ratio=3.0))
        ok, why = _decide(_m(**self.WORKING), cand, escape=True, evidence=True)
        assert ok and 'without the RMS guard' in why

    def test_unknown_merit_is_not_held_against_a_candidate(self):
        """Every candidate the joint fit produces carries the fields; a
        stubbed or legacy one without them keeps the old behaviour."""
        ok, _ = _decide(_m(**self.WORKING), _m(6.0, 900), escape=True,
                        evidence=True)
        assert ok

    def test_refused_bypass_falls_through_to_the_material_gain_floor(self):
        """A chance-level candidate that nevertheless beats a thin incumbent's
        RMS by the escape margin with more matches still wins on the numbers."""
        thin = _m(14.0, 500)
        ok, why = _decide(thin, _m(**self.CHANCE_FIT), escape=True, evidence=True)
        assert ok and 'no merit of its own' in why and 'beats' in why


class TestChanceLevelIncumbentIsDiscredited:
    """Rule 3's trigger widened: an incumbent that matched the live buffer at
    chance level in consecutive runs (incumbent_chance) is as discredited as
    one that failed the bright-anchor gate — on #93's obstructed rig the
    anchor gate read None all night, so this is the verdict that reaches the
    rule there."""

    def _incumbent(self, **over):
        return _m(10.9, 606, n_images=60, span=62.6, final_tol_px=16.5,
                  chance_ratio=1.04, **over)

    def _candidate(self):
        return _m(8.0, 700, n_images=30, span=60.0, final_tol_px=16.0,
                  chance_ratio=2.5)   # 0.5 of tolerance: credible

    def test_chance_level_twice_lifts_the_rms_veto(self):
        ok, why = _decide(self._incumbent(), self._candidate(), escape=True,
                          incumbent_chance_level_twice=True)
        assert ok
        assert 'chance level in consecutive runs' in why
        assert 'does not get an RMS veto' in why

    def test_without_the_verdict_the_normal_comparison_applies(self):
        ok, _ = _decide(self._incumbent(), self._candidate(), escape=True)
        assert ok   # 8.0 beats 10.9 by more than 15 % with more matches
        ok, why = _decide(self._incumbent(), _m(10.5, 700, n_images=30,
                                                 span=60.0), escape=True)
        assert not ok and 'material' in why

    def test_the_flag_does_nothing_without_an_escape(self):
        ok, why = _decide(self._incumbent(), _m(14.0, 400), escape=False,
                          incumbent_chance_level_twice=True)
        assert not ok and 'worse' in why

    def test_guided_incumbent_is_exempt(self):
        """Held to the normal comparison instead, where a non-material gain
        loses."""
        cand = _m(9.5, 700, n_images=30, span=60.0, final_tol_px=16.0,
                  chance_ratio=2.5)
        ok, why = _decide(self._incumbent(provenance='guided'), cand,
                          escape=True, incumbent_chance_level_twice=True)
        assert not ok and 'RMS veto' not in why and 'material' in why

    def test_either_discredit_suffices(self):
        ok, why = _decide(self._incumbent(), self._candidate(), escape=True,
                          incumbent_failed_anchors=True,
                          incumbent_chance_level_twice=True)
        assert ok and 'bright-anchor check' in why

    @pytest.mark.parametrize('discredit', [
        dict(incumbent_failed_anchors=True),
        dict(incumbent_chance_level_twice=True),
    ])
    def test_a_candidate_without_merit_gets_no_bypass_from_either_discredit(
            self, discredit):
        """The Sep 19 shape on the #93 rig: a candidate at ~1.0x chance and
        RMS = tol / sqrt(2) over a discredited incumbent. It must not walk in
        on the incumbent's lost veto; it is held to the normal comparison,
        where it is worse on RMS than the incumbent it would replace."""
        no_merit = _m(11.6, 644, n_images=60, span=62.6, final_tol_px=16.5,
                      chance_ratio=1.04)
        ok, why = _decide(self._incumbent(), no_merit, escape=True, **discredit)
        assert not ok
        assert 'no merit of its own' in why and 'RMS veto' not in why
