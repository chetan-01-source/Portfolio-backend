"""Exhaustive tests for the three-stage semantic cache.

Tests cover:
  1. _extract_keywords — stopword removal, edge cases
  2. _extract_entities — known entity detection
  3. _entities_conflict — same-category conflict detection
  4. _keyword_overlap — Jaccard similarity maths
  5. should_accept_cache_hit — full three-stage decision (the primary API)
  6. Cache decision matrix — realistic Chetan portfolio query pairs
  7. Edge cases
"""

import pytest

from app.cache.semantic import (
    _entities_conflict,
    _extract_entities,
    _extract_keywords,
    _keyword_overlap,
    should_accept_cache_hit,
)

# ─────────────────────────────────────────────────────────────────────
#  1. _extract_keywords
# ─────────────────────────────────────────────────────────────────────

class TestExtractKeywords:
    """Verify stopword removal and keyword extraction."""

    def test_simple_query(self):
        kw = _extract_keywords("Tell me about the CSAT project")
        assert "csat" in kw
        assert "project" in kw
        assert "tell" not in kw
        assert "me" not in kw
        assert "about" not in kw
        assert "the" not in kw

    def test_cometchat_query(self):
        kw = _extract_keywords("What is Chetan's experience at CometChat?")
        assert "cometchat" in kw
        # "experience" is now a stopword (generic professional filler)
        assert "experience" not in kw
        # "chetan" is a stopword
        assert "chetan" not in kw

    def test_empty_string(self):
        assert _extract_keywords("") == set()

    def test_only_stopwords(self):
        kw = _extract_keywords("tell me about his details please")
        assert kw == set()

    def test_mixed_case(self):
        kw = _extract_keywords("React TypeScript FastAPI MongoDB")
        assert kw == {"react", "typescript", "fastapi", "mongodb"}

    def test_numbers_preserved(self):
        kw = _extract_keywords("deployed on AWS EC2 with Python 3.11")
        assert "aws" in kw
        assert "ec2" in kw
        assert "python" in kw

    def test_project_names(self):
        for name in ["CHET.ai", "GitTogether", "WhatsApp Clone", "Britannia Campaign Engine"]:
            kw = _extract_keywords(name)
            assert len(kw) > 0, f"No keywords extracted from '{name}'"

    def test_schbang_query(self):
        kw = _extract_keywords("What work has Chetan done at Schbang?")
        assert "schbang" in kw
        # "work" and "done" are now stopwords
        assert "work" not in kw
        assert "done" not in kw

    def test_generic_terms_are_stopwords(self):
        """Generic HR/professional terms should be removed."""
        kw = _extract_keywords("role responsibilities overview info working experience")
        assert kw == set()

    def test_skill_query(self):
        kw = _extract_keywords("Does Chetan know Docker and Kubernetes?")
        assert "docker" in kw
        assert "kubernetes" in kw


# ─────────────────────────────────────────────────────────────────────
#  2. _extract_entities
# ─────────────────────────────────────────────────────────────────────

class TestExtractEntities:
    """Verify known entity detection."""

    def test_company_detection(self):
        entities = _extract_entities("Experience at Schbang")
        assert "company:schbang" in entities

    def test_cometchat_detection(self):
        entities = _extract_entities("CometChat work")
        assert "company:cometchat" in entities

    def test_project_detection(self):
        assert "project:csat" in _extract_entities("CSAT automation project")
        assert "project:gittogether" in _extract_entities("GitTogether platform")
        assert "project:britannia" in _extract_entities("Britannia Campaign Engine")
        assert "project:whatsapp-clone" in _extract_entities("WhatsApp Clone project")

    def test_chet_ai_detection(self):
        assert "project:chet.ai" in _extract_entities("CHET.ai project")
        assert "project:chet.ai" in _extract_entities("Tell me about CHET")

    def test_tech_domain_detection(self):
        entities = _extract_entities("WebRTC voice calling system")
        assert "tech:voice" in entities

    def test_multiple_entities(self):
        entities = _extract_entities("CSAT project at Schbang")
        assert "project:csat" in entities
        assert "company:schbang" in entities

    def test_no_entities(self):
        entities = _extract_entities("Hello, how are you?")
        assert len(entities) == 0

    def test_case_insensitive(self):
        entities = _extract_entities("cometchat SCHBANG csat")
        assert "company:cometchat" in entities
        assert "company:schbang" in entities
        assert "project:csat" in entities


# ─────────────────────────────────────────────────────────────────────
#  3. _entities_conflict
# ─────────────────────────────────────────────────────────────────────

class TestEntitiesConflict:
    """Verify entity conflict detection."""

    def test_same_company_no_conflict(self):
        assert not _entities_conflict({"company:schbang"}, {"company:schbang"})

    def test_different_companies_conflict(self):
        assert _entities_conflict({"company:schbang"}, {"company:cometchat"})

    def test_different_projects_conflict(self):
        assert _entities_conflict({"project:csat"}, {"project:gittogether"})

    def test_different_categories_no_conflict(self):
        # company vs project is NOT a conflict
        assert not _entities_conflict({"company:schbang"}, {"project:csat"})

    def test_empty_sets_no_conflict(self):
        assert not _entities_conflict(set(), {"company:schbang"})
        assert not _entities_conflict({"project:csat"}, set())
        assert not _entities_conflict(set(), set())

    def test_mixed_with_conflict(self):
        a = {"company:schbang", "project:csat"}
        b = {"company:cometchat", "project:csat"}
        assert _entities_conflict(a, b)  # companies conflict

    def test_mixed_no_conflict(self):
        a = {"company:schbang", "project:csat"}
        b = {"company:schbang", "tech:voice"}
        assert not _entities_conflict(a, b)

    def test_tech_domain_conflict(self):
        a = {"tech:voice"}
        b = {"tech:webflow"}
        assert _entities_conflict(a, b)


# ─────────────────────────────────────────────────────────────────────
#  4. _keyword_overlap (Jaccard)
# ─────────────────────────────────────────────────────────────────────

class TestKeywordOverlap:
    """Verify Jaccard similarity maths."""

    def test_identical_queries(self):
        overlap = _keyword_overlap("CSAT project", "CSAT project")
        assert overlap == 1.0

    def test_zero_overlap(self):
        overlap = _keyword_overlap("CSAT project", "GitTogether platform")
        assert overlap == 0.0

    def test_both_empty_after_stopwords(self):
        overlap = _keyword_overlap("tell me about", "please share details")
        assert overlap == 0.0

    def test_one_empty(self):
        overlap = _keyword_overlap("", "CSAT project")
        assert overlap == 0.0

    def test_symmetric(self):
        a = "CometChat SDK"
        b = "Schbang platform"
        assert _keyword_overlap(a, b) == _keyword_overlap(b, a)

    def test_exact_same(self):
        assert _keyword_overlap("CSAT project", "CSAT project") == 1.0

    def test_disjoint(self):
        overlap = _keyword_overlap("docker kubernetes", "react angular")
        assert overlap == 0.0


# ─────────────────────────────────────────────────────────────────────
#  5. should_accept_cache_hit — full three-stage decision
# ─────────────────────────────────────────────────────────────────────

VECTOR_SIM_HIGH = 0.95  # typical for same-domain queries


class TestShouldAcceptCacheHit:
    """Test the unified three-stage acceptance function."""

    def test_low_vector_similarity_rejects(self):
        accepted, reason = should_accept_cache_hit(
            "CSAT project", "CSAT project", vector_similarity=0.80
        )
        assert accepted is False
        assert "vector sim" in reason

    def test_entity_conflict_rejects_same_category(self):
        """Two different companies → entity conflict (same category)."""
        accepted, reason = should_accept_cache_hit(
            "Schbang work details",
            "CometChat work details",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is False
        assert "entity conflict" in reason

    def test_cross_category_still_rejected(self):
        """CSAT (project) vs CometChat (company) → different categories,
        but keyword overlap catches it anyway."""
        accepted, reason = should_accept_cache_hit(
            "CSAT project details",
            "CometChat experience overview",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is False

    def test_low_keyword_overlap_rejects(self):
        accepted, reason = should_accept_cache_hit(
            "Docker Kubernetes deployment",
            "React Angular frontend",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is False
        assert "keyword overlap" in reason

    def test_matching_query_accepts(self):
        accepted, reason = should_accept_cache_hit(
            "CSAT automation project",
            "CSAT automation platform",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is True
        assert "accepted" in reason


# ─────────────────────────────────────────────────────────────────────
#  6. Cache decision matrix — realistic portfolio queries
# ─────────────────────────────────────────────────────────────────────


class TestCacheDecisionShouldMiss:
    """Query pairs that MUST NOT share a cache entry."""

    @pytest.mark.parametrize(
        "new_query, cached_query",
        [
            # ── Different projects (entity conflict) ──
            ("Tell me about CSAT project", "CometChat experience"),
            ("CSAT automation details", "GitTogether project"),
            ("Britannia Campaign Engine", "WhatsApp Clone project"),
            ("What is the CHET.ai project?", "GitTogether overview"),
            ("GitTogether project details", "Britannia Campaign Engine features"),
            ("WhatsApp Clone project", "CSAT automation platform"),

            # ── Different companies (entity conflict) ──
            ("Experience at Schbang", "Experience at CometChat"),
            ("What did Chetan do at CometChat?", "Schbang work details"),
            ("Schbang role", "CometChat role"),

            # ── Project vs Company with entity conflict ──
            ("CometChat experience", "CHET.ai project details"),

            # ── Different tech domains ──
            ("WebRTC voice calling system", "Webflow CMS architecture"),

            # ── Completely unrelated ──
            ("Docker and Kubernetes skills", "Britannia Campaign Engine"),
            ("Python FastAPI backend", "WhatsApp Clone project"),

            # ── Hire/Contact vs Technical ──
            ("How to hire Chetan?", "CSAT project details"),
            ("Salary expectations", "Britannia Campaign Engine"),
        ],
        ids=lambda x: x[:50],
    )
    def test_should_miss(self, new_query, cached_query):
        accepted, reason = should_accept_cache_hit(
            new_query, cached_query, vector_similarity=VECTOR_SIM_HIGH
        )
        assert accepted is False, (
            f"SHOULD MISS but accepted: {reason}\n"
            f"  new:    {new_query!r}\n"
            f"  cached: {cached_query!r}"
        )


class TestCacheDecisionShouldHit:
    """Query pairs that SHOULD share a cache entry."""

    @pytest.mark.parametrize(
        "new_query, cached_query",
        [
            # ── Same project, rephrased ──
            ("CSAT automation project details", "CSAT automation platform info"),
            ("GitTogether project overview", "overview GitTogether project"),
            ("Britannia Campaign Engine details", "Britannia Campaign Engine features"),

            # ── Same skill, rephrased ──
            ("React and TypeScript", "TypeScript and React"),
            ("Python FastAPI backend", "FastAPI Python backend"),
            ("Docker deployment setup", "deployment Docker setup"),
        ],
        ids=lambda x: x[:50],
    )
    def test_should_hit(self, new_query, cached_query):
        accepted, reason = should_accept_cache_hit(
            new_query, cached_query, vector_similarity=VECTOR_SIM_HIGH
        )
        assert accepted is True, (
            f"SHOULD HIT but rejected: {reason}\n"
            f"  new:    {new_query!r}\n"
            f"  cached: {cached_query!r}"
        )


# ─────────────────────────────────────────────────────────────────────
#  7. Edge Cases
# ─────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_single_keyword_both(self):
        accepted, _ = should_accept_cache_hit("CSAT", "CSAT", vector_similarity=0.99)
        assert accepted is True

    def test_greeting_queries_reject(self):
        accepted, _ = should_accept_cache_hit("Hello!", "Hi there", vector_similarity=0.95)
        assert accepted is False

    def test_question_word_variations_same_topic(self):
        accepted, _ = should_accept_cache_hit(
            "What is the CSAT automation project?",
            "How does the CSAT automation work?",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is True

    def test_cache_query_with_history_blob_rejected(self):
        """If somehow a cached query contains history (legacy), it should be treated carefully."""
        bloated = (
            "Recent chat summary:\n"
            "User: Tell me about his current work\n"
            "Assistant: Chetan is at Schbang.\n\n"
            "Current user question: cometchat experience"
        )
        accepted, reason = should_accept_cache_hit(
            "CSAT project details",
            bloated,
            vector_similarity=VECTOR_SIM_HIGH,
        )
        # Should miss — entities conflict (cometchat vs csat)
        assert accepted is False

    def test_same_project_at_same_company(self):
        """CSAT at Schbang should not conflict with itself."""
        accepted, _ = should_accept_cache_hit(
            "CSAT automation project at Schbang",
            "Schbang CSAT platform",
            vector_similarity=VECTOR_SIM_HIGH,
        )
        assert accepted is True

    def test_empty_queries(self):
        accepted, _ = should_accept_cache_hit("", "", vector_similarity=0.99)
        assert accepted is False

    def test_unicode_handling(self):
        kw = _extract_keywords("Chetan's résumé — AI engineer")
        assert "ai" in kw
        assert "engineer" in kw

    def test_very_long_query(self):
        long_q = " ".join(
            ["CSAT", "CometChat", "GitTogether", "Britannia", "Schbang", "WhatsApp"]
        )
        kw = _extract_keywords(long_q)
        assert "csat" in kw
        assert "cometchat" in kw
        assert "gittogether" in kw

    def test_numbers_only_query(self):
        kw = _extract_keywords("2024 2025 2026")
        assert len(kw) == 3


# ─────────────────────────────────────────────────────────────────────
#  8. Regression — exact screenshot bug
# ─────────────────────────────────────────────────────────────────────

class TestRegression:
    """Reproduce the exact bug scenarios from production screenshots."""

    def test_cometchat_cached_csat_question_misses(self):
        """Screenshot bug: 'cometchat experience' cached, then 'CSAT project' returned same."""
        accepted, reason = should_accept_cache_hit(
            new_query="help me with the CSAT project and its details",
            cached_query="cometchat experience",
            vector_similarity=0.95,
        )
        assert accepted is False
        # Rejected via keyword overlap (CSAT=project, CometChat=company are different categories)

    def test_schbang_cached_cometchat_question_misses(self):
        accepted, reason = should_accept_cache_hit(
            new_query="What did Chetan do at CometChat?",
            cached_query="Schbang role and work",
            vector_similarity=0.94,
        )
        assert accepted is False

    def test_broad_schbang_cached_specific_csat_misses(self):
        """Broad Schbang answer cached → narrow CSAT question must miss."""
        accepted, reason = should_accept_cache_hit(
            new_query="Tell me about CSAT automation platform details",
            cached_query="What is Chetan doing at Schbang currently?",
            vector_similarity=0.93,
        )
        assert accepted is False

    def test_gittogether_cached_britannia_misses(self):
        accepted, reason = should_accept_cache_hit(
            new_query="Britannia Campaign Engine features",
            cached_query="GitTogether project overview",
            vector_similarity=0.94,
        )
        assert accepted is False
        assert "entity conflict" in reason
