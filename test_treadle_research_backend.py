"""The new backend must research with PREP, rather than merely label itself an agent."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from packs.market_research_next.research import (
    EXTERNAL_CATEGORIES, EXTERNAL_TOOLS, LocalResearchBackend, ResearchError,
    SIMULATION_NOTICE,
)
from packs.market_research_next.render import render_html
from treadle.core import Evidence
from treadle.harness.agent import AgentResult, MAX_STEPS_CEILING, Step, select_tools
from treadle.run import Report
from treadle.workflow import ReviewIssue


def context(section_key):
    return {
        "prep": {
            "venture": {"attributes": {
                "product": {"value": "AI dental receptionist", "status": "customer_statement"},
                "geography": {"value": "California", "status": "working_assumption"},
            }},
            "research_spec": {section_key: {
                "scope": "Independent dental practices; exclude hospital systems",
                "questions": ["Who pays for after-hours appointment handling?"],
                "assumptions_to_test": ["Missed calls represent lost appointments"],
                "required_deliverables": ["Explain the evidence and unresolved uncertainty"],
            }},
        },
        "target_personas": {"findings": ["Practice owners control purchasing"]},
    }


def valid_section():
    return {
        "title": "Research findings", "narrative": "Practice owners may buy the product.",
        "findings": ["Buying authority needs validation"],
        "assumptions": ["California is the working geography"],
        "uncertainties": ["Willingness to pay is unknown"],
    }


def complex_context(section_key):
    ctx = context(section_key)
    ctx["prep"]["business_context"] = {
        "schema_version": "market_research.business_context/1",
        "object": {
            "version": "market_research.business/2", "company_alias": "Hybrid Studio",
            "nodes": [
                {"id": "company", "kind": "company", "label": "Hybrid Studio",
                 "parent_id": None, "statement_ids": ["input_1"]},
                {"id": "classes", "kind": "offering", "label": "Studio and online classes",
                 "parent_id": "company", "statement_ids": ["input_1"]},
                {"id": "memberships", "kind": "revenue_stream", "label": "Memberships",
                 "parent_id": "classes", "statement_ids": ["input_1"]},
                {"id": "drop_in", "kind": "revenue_stream", "label": "Drop-in fees",
                 "parent_id": "classes", "statement_ids": ["input_1"]},
            ],
            "facts": [
                {"id": "locations", "node_id": "classes", "key": "locations",
                 "value": ["Oakland", "Berkeley"], "basis": "stated",
                 "statement_ids": ["input_1"], "qualifiers": {"geography": "California"}},
                {"id": "channels", "node_id": "classes", "key": "delivery_channels",
                 "value": ["in person", "online"], "basis": "stated",
                 "statement_ids": ["input_1"], "qualifiers": {}},
                {"id": "audience", "node_id": "classes", "key": "buyer_role",
                 "value": "Busy local professionals", "basis": "inferred",
                 "statement_ids": ["input_1"], "qualifiers": {}},
            ],
            "relationships": [
                {"id": "classes_memberships", "from_id": "classes", "to_id": "memberships",
                 "kind": "generates", "basis": "stated", "statement_ids": ["input_1"],
                 "qualifiers": {}},
                {"id": "classes_drop_in", "from_id": "classes", "to_id": "drop_in",
                 "kind": "generates", "basis": "stated", "statement_ids": ["input_1"],
                 "qualifiers": {}},
            ],
            "gaps": [{"id": "pricing", "node_id": "classes",
                      "question": "What are the membership and drop-in prices?",
                      "section_ids": [section_key]}],
        },
        "statements": [{"id": "input_1", "text": "Our Oakland and Berkeley studios offer "
                        "classes online and in person, with memberships and drop-in fees."}],
        "statement_paths": {"input_1": "/description"},
        "construction": {"stop_reason": "budget_exhausted", "expansion_complete": False,
                         "quarantined_count": 1},
        "readiness": {"report_ready": False, "sections": {section_key: {
            "status": "needs_input", "ready_for_research": False, "report_ready": False,
            "missing_requirements": [{"id": "revenue.pricing", "resolution": "external_research",
                                      "question": "What are the membership and drop-in prices?"}],
            "node_ids": ["classes"], "fact_ids": [], "relationship_ids": [],
        }}},
        "limitations": ["Some business details could not be expanded within the preparation limit."],
    }
    return ctx


def observed_result(*, completed=True, failed=False):
    result = AgentResult(
        goal="Research", answer="Observed public discussion mentions missed calls.",
        completed=completed, stop_reason="done" if completed else "budget_exhausted",
        evidence=[Evidence(
            source="web_search", category="scrape", count=1,
            payload=[{"url": "https://example.org/discussion", "snippet": "Missed calls"}],
        )],
        steps=[Step(0, "web_search", {}, True, "One result")],
    )
    if failed:
        result.evidence.append(Evidence.empty("fetch_page", "scrape", error="timeout"))
        result.steps.append(Step(1, "fetch_page", {}, False, "timeout", error="timeout"))
    return result


class TestResearchBackend(unittest.TestCase):
    def test_full_business_graph_and_section_gaps_reach_research_and_writing(self):
        captured = []

        def model(system, user, **kwargs):
            captured.append((system, json.loads(user)))
            return valid_section()

        ctx = complex_context("market_size")
        before = copy.deepcopy(ctx)
        with patch("packs.market_research_next.research.run_agent",
                   return_value=observed_result()) as agent:
            output = LocalResearchBackend(model)("market_size", ctx)
        self.assertEqual(ctx, before)
        business = ctx["prep"]["business_context"]
        research_request = json.loads(agent.call_args.kwargs["context"])
        for request in (research_request, captured[0][1]):
            self.assertEqual(request["context"]["prep"]["business_context"], business)
            self.assertEqual(request["section_input_readiness"],
                             business["readiness"]["sections"]["market_size"])
            graph = request["context"]["prep"]["business_context"]["object"]
            self.assertEqual([item["label"] for item in graph["nodes"]
                              if item["kind"] == "revenue_stream"], ["Memberships", "Drop-in fees"])
            self.assertEqual(graph["facts"][0]["value"], ["Oakland", "Berkeley"])
            self.assertEqual(graph["facts"][1]["value"], ["in person", "online"])
            self.assertEqual(graph["facts"][2]["basis"], "inferred")
        self.assertIn("full accepted business graph", agent.call_args.args[0])
        self.assertIn("stated versus inferred", captured[0][0])
        # needs_input is a starting-information gap, not a hard stop or a prompt
        # for the customer. External research still runs and can return findings.
        self.assertEqual(output["research_status"], "completed")
        self.assertIn(business["limitations"][0], output["uncertainties"])
        self.assertIn("Starting information did not answer: What are the membership and drop-in prices?",
                      output["uncertainties"])

    def test_missing_questions_are_section_specific_and_not_technical_ids(self):
        ctx = complex_context("summary")
        ctx["prep"]["business_context"]["readiness"]["sections"]["revenue"] = {
            "missing_requirements": [{"id": "revenue.adoption", "question": "What is the adoption rate?"}]
        }
        output = LocalResearchBackend(lambda *a, **kw: valid_section())("summary", ctx)
        self.assertNotIn("revenue.pricing", " ".join(output["uncertainties"]))
        self.assertNotIn("What is the adoption rate?", " ".join(output["uncertainties"]))

    def test_malformed_business_context_does_not_silently_use_flat_attributes(self):
        cases = [None, {}, {"object": {}, "readiness": {"sections": {}}}]
        for value in cases:
            ctx = context("summary")
            ctx["prep"]["business_context"] = value
            with self.subTest(value=value), \
                 patch("packs.market_research_next.research._default_model") as model:
                with self.assertRaises(ResearchError):
                    LocalResearchBackend()("summary", ctx)
                model.assert_not_called()

    def test_report_discloses_and_escapes_initial_business_limitations(self):
        ctx = complex_context("summary")
        business = ctx["prep"]["business_context"]
        limitation = "Description still contains <script>alert('limit')</script> assumptions."
        question = "How does <img src=x onerror=alert(1)> earn revenue?"
        business["limitations"] = [limitation, limitation]
        business["readiness"]["sections"] = {
            key: {"missing_requirements": [{"id": "internal." + key, "question": question}]}
            for key in ("summary", "revenue")
        }
        ctx["prep"]["venture"]["attributes"]["geography"] = {"basis": "unknown", "value": None}
        html = render_html(Report(type="example", result={"prep": ctx["prep"]}, sections=None))
        self.assertIn("Business description limitations", html)
        self.assertIn("Section findings may address these questions through research", html)
        self.assertEqual(html.count("How does &lt;img"), 1)
        self.assertEqual(html.count("Description still contains &lt;script&gt;"), 1)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img ", html)
        self.assertNotIn("internal.summary", html)
        self.assertIn("Business details still unknown:", html)
        self.assertIn("geography", html)

    def test_prep_and_dependencies_reach_both_research_and_structuring(self):
        captured = []

        def model(system, user, **kwargs):
            captured.append((system, json.loads(user), kwargs))
            return valid_section()

        ctx = context("competitive_landscape")
        before = copy.deepcopy(ctx)
        result = observed_result()
        with patch("packs.market_research_next.research.run_agent", return_value=result) as agent:
            output = LocalResearchBackend(model, max_steps=4).research(
                "competitive_landscape", ctx, issues=["Clarify buyer scope"],
            )
        self.assertEqual(ctx, before)
        research_input = json.loads(agent.call_args.kwargs["context"])
        self.assertEqual(research_input["context"], ctx)
        self.assertEqual(research_input["issues_to_address"], ["Clarify buyer scope"])
        self.assertEqual(research_input["research_specification"],
                         ctx["prep"]["research_spec"]["competitive_landscape"])
        self.assertEqual(len(captured), 1)
        structured_input = captured[0][1]
        self.assertEqual(structured_input["context"], ctx)
        self.assertEqual(structured_input["external_research"]["answer"], result.answer)
        self.assertEqual(structured_input["external_research"]["evidence"],
                         [item.to_dict() for item in result.evidence])
        self.assertEqual(output["research_status"], "completed")
        self.assertEqual(captured[0][2]["tier"], "reasoning")
        self.assertEqual(agent.call_args.kwargs["max_steps"], 4)

    def test_each_external_section_masks_explicit_registered_tools(self):
        from tools import TOOL_REGISTRY

        registered = {meta.category for meta in TOOL_REGISTRY.values()}
        for key, categories in EXTERNAL_CATEGORIES.items():
            with self.subTest(section=key):
                self.assertTrue(set(categories).issubset(registered))
                with patch("packs.market_research_next.research.run_agent",
                           return_value=observed_result()) as agent:
                    LocalResearchBackend(lambda *a, **kw: valid_section())(key, context(key))
                self.assertEqual(agent.call_args.kwargs["allowed_categories"], list(categories))
                self.assertEqual(agent.call_args.kwargs["allowed_tools"], list(EXTERNAL_TOOLS[key]))
                selected = select_tools(allowed_tools=list(EXTERNAL_TOOLS[key]),
                                        allowed_categories=list(categories))
                self.assertEqual({tool.name for tool in selected}, set(EXTERNAL_TOOLS[key]))

    def test_real_agent_rejects_excluded_tools_even_when_model_requests_them(self):
        from tools import TOOL_REGISTRY

        excluded = ("fetch_page", "trustpilot_reviews", "enrich_competitor",
                    "enrich_competitors_batch")
        for key in EXTERNAL_TOOLS:
            for forbidden in excluded:
                with self.subTest(section=key, tool=forbidden), ExitStack() as stack:
                    # Exercise the real harness and its registry lookup. Mock only
                    # model decisions and external calls so this never uses providers.
                    denied = {
                        name: stack.enter_context(patch.object(TOOL_REGISTRY[name], "fn"))
                        for name in excluded
                    }
                    search = stack.enter_context(patch.object(
                        TOOL_REGISTRY["web_search"], "fn",
                        return_value=observed_result().evidence[0],
                    ))
                    stack.enter_context(patch("treadle.harness.agent.call_next", side_effect=[
                        {"tool": forbidden, "args": {"url": "http://127.0.0.1/private"}},
                        {"tool": "web_search", "args": {"query": "dental receptionist"}},
                        {"done": True, "answer": "One public search result."},
                    ]))
                    captured = []

                    def model(system, user, **kwargs):
                        captured.append(json.loads(user))
                        return valid_section()

                    output = LocalResearchBackend(model, max_steps=3)(key, context(key))
                    for tool in denied.values():
                        tool.assert_not_called()
                    search.assert_called_once_with(query="dental receptionist")
                    steps = captured[0]["external_research"]["steps"]
                    self.assertEqual(steps[0]["error"], "invalid_tool")
                    self.assertFalse(steps[0]["ok"])
                    self.assertEqual(output["research_status"], "partial")

    def test_workflow_review_issues_reach_research_and_structuring_as_json(self):
        captured = []

        def model(system, user, **kwargs):
            captured.append(json.loads(user))
            return valid_section()

        issue = ReviewIssue("scope_conflict", "Use the same geography", ("market_size",))
        with patch("packs.market_research_next.research.run_agent",
                   return_value=observed_result()) as agent:
            LocalResearchBackend(model)("market_size", context("market_size"), issues=(issue,))
        self.assertEqual(json.loads(agent.call_args.kwargs["context"])["issues_to_address"],
                         [issue.as_dict()])
        self.assertEqual(captured[0]["issues_to_address"], [issue.as_dict()])

    def test_default_model_uses_the_existing_llm_adapter_without_reasking(self):
        with patch("treadle.llm.call_json", return_value=valid_section()) as model:
            LocalResearchBackend()("summary", context("summary"))
        model.assert_called_once()
        self.assertEqual(model.call_args.kwargs["tier"], "critical")
        self.assertEqual(model.call_args.kwargs["max_retries"], 0)

    def test_summary_uses_upstream_results_without_another_research_loop(self):
        captured = []

        def model(system, user, **kwargs):
            captured.append((json.loads(user), kwargs))
            return valid_section()

        ctx = context("summary")
        ctx["market_size"] = {"narrative": "Market estimate remains conditional"}
        with patch("packs.market_research_next.research.run_agent") as agent:
            output = LocalResearchBackend(model)("summary", ctx)
        agent.assert_not_called()
        self.assertEqual(captured[0][0]["context"]["market_size"], ctx["market_size"])
        self.assertEqual(captured[0][1]["tier"], "critical")
        self.assertEqual(output["research_status"], "derived")

    def test_simulated_perspectives_get_a_visible_backend_owned_disclosure(self):
        with patch("packs.market_research_next.research.run_agent") as agent:
            output = LocalResearchBackend(lambda *a, **kw: valid_section())(
                "multi_perspective", context("multi_perspective"),
            )
        agent.assert_not_called()
        self.assertEqual(output["research_status"], "simulated")
        self.assertTrue(output["narrative"].startswith(SIMULATION_NOTICE))
        self.assertIn("simulated", output["title"])

    def test_no_evidence_or_heuristics_do_not_become_research(self):
        cases = [
            [],
            [Evidence.empty("web_search", "scrape", error="offline")],
            [Evidence("estimate", "geo", 1, {"count": 999}, skeleton=True)],
            [Evidence("web_search", "scrape", 1, [])],
        ]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result = AgentResult("Research", "Looks complete", evidence=evidence,
                                     completed=True, stop_reason="done")
                with patch("packs.market_research_next.research.run_agent", return_value=result), \
                     patch("packs.market_research_next.research._default_model") as model:
                    with self.assertRaisesRegex(ResearchError, "no usable external evidence"):
                        LocalResearchBackend()("market_size", context("market_size"))
                model.assert_not_called()

    def test_partial_research_discloses_budget_and_tool_failures(self):
        for completed, failed in ((False, False), (True, True)):
            with self.subTest(completed=completed, failed=failed):
                result = observed_result(completed=completed, failed=failed)
                with patch("packs.market_research_next.research.run_agent", return_value=result):
                    output = LocalResearchBackend(lambda *a, **kw: valid_section())(
                        "customer_voice", context("customer_voice"),
                    )
                self.assertEqual(output["research_status"], "partial")
                self.assertIn("incomplete", output["uncertainties"][0])
                if failed:
                    self.assertIn("Tool failures: 1", output["research_note"])

    def test_input_transforms_alone_are_not_external_research(self):
        for source in ("filter_aggregator_domains", "extract_prices", "extract_structured",
                       "enrich_competitors_batch"):
            with self.subTest(source=source):
                result = observed_result()
                result.evidence = [Evidence(source, "scrape", 1, {"invented": "input"})]
                with patch("packs.market_research_next.research.run_agent", return_value=result), \
                     patch("packs.market_research_next.research._default_model") as model:
                    with self.assertRaisesRegex(ResearchError, "no usable external evidence"):
                        LocalResearchBackend()("competitive_landscape", context("competitive_landscape"))
                model.assert_not_called()

    def test_agent_and_model_exceptions_surface_without_fallback(self):
        with patch("packs.market_research_next.research.run_agent", side_effect=OSError("offline")):
            with self.assertRaisesRegex(ResearchError, "external research failed") as exc:
                LocalResearchBackend()("market_size", context("market_size"))
            self.assertIsInstance(exc.exception.__cause__, OSError)

        def unavailable(*args, **kwargs):
            raise TimeoutError("model unavailable")

        with self.assertRaisesRegex(ResearchError, "section structuring failed") as exc:
            LocalResearchBackend(unavailable)("summary", context("summary"))
        self.assertIsInstance(exc.exception.__cause__, TimeoutError)

    def test_planner_stop_without_answer_cannot_claim_completed_research(self):
        # The existing harness can set completed=True when its planner JSON fails.
        result = observed_result()
        result.answer = ""
        with patch("packs.market_research_next.research.run_agent", return_value=result):
            output = LocalResearchBackend(lambda *a, **kw: valid_section())(
                "market_size", context("market_size"),
            )
        self.assertEqual(output["research_status"], "partial")
        self.assertIn("did not return a final answer", output["research_note"])

    def test_malformed_model_results_are_rejected(self):
        cases = [None, [], {}, {"_parse_error": "invalid JSON"},
                 {**valid_section(), "narrative": " "},
                 {**valid_section(), "findings": "not a list"},
                 {**valid_section(), "assumptions": [None]},
                 {**valid_section(), "metrics": {"tam": float("nan")}}]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ResearchError):
                    LocalResearchBackend(lambda *a, **kw: value)("summary", context("summary"))

    def test_budget_is_bounded_and_no_automatic_correction_rounds_run(self):
        backend = LocalResearchBackend(lambda *a, **kw: valid_section(), max_steps=1000)
        with patch("packs.market_research_next.research.run_agent",
                   return_value=observed_result(completed=False)) as agent:
            backend("market_size", context("market_size"), issues=["Recheck scope"])
        agent.assert_called_once()
        self.assertEqual(agent.call_args.kwargs["max_steps"], MAX_STEPS_CEILING)
        for invalid in (0, -1, True, 2.5, "6"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LocalResearchBackend(max_steps=invalid)

    def test_missing_prep_or_section_spec_is_an_error_before_any_model_call(self):
        cases = [{}, {"prep": {}}, {"prep": {"venture": {"attributes": {}}}}]
        with patch("packs.market_research_next.research._default_model") as model:
            for ctx in cases:
                with self.subTest(context=ctx), self.assertRaises(ResearchError):
                    LocalResearchBackend()("summary", ctx)
            with self.assertRaisesRegex(ResearchError, "Unknown research section"):
                LocalResearchBackend()("unknown", context("summary"))
        model.assert_not_called()

    def test_the_write_up_has_room_for_a_full_section(self):
        # Live 2026-10-30: customer voice's write-up stopped mid-string at 3,500 tokens.
        captured = []

        def model(system, user, **kwargs):
            captured.append(kwargs)
            return valid_section()

        with patch("packs.market_research_next.research.run_agent",
                   return_value=observed_result()):
            LocalResearchBackend(model).research("competitive_landscape",
                                                 context("competitive_landscape"))
        self.assertGreaterEqual(captured[-1]["max_tokens"], 6000)

    def test_community_hint_matches_what_reddit_search_can_reach(self):
        def found(source):
            ctx = context("customer_voice")
            ctx["community_discovery"] = {"data": {"source": source, "subreddits": [
                {"name": "Coffee_Shop"}, {"name": "barista"}]}}
            return ctx

        def goal(ctx):
            with patch("packs.market_research_next.research.run_agent",
                       return_value=observed_result()) as agent:
                LocalResearchBackend(lambda *a, **k: valid_section()).research(
                    "customer_voice", ctx)
            return agent.call_args.args[0]

        with_keys = goal(found("reddit"))
        self.assertIn("r/Coffee_Shop", with_keys)
        self.assertIn("subreddit:", with_keys)
        # Without Reddit credentials, reddit_mentions answers 403: name the communities,
        # never send the agent to search inside them through Reddit.
        archive = goal(found("open_archive"))
        self.assertIn("r/Coffee_Shop", archive)
        self.assertNotIn("subreddit:", archive)
        self.assertNotIn("reddit_mentions", archive)


if __name__ == "__main__":
    unittest.main()
