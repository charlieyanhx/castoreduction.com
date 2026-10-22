"""Replaceable execution policies for a declared report workflow.

The deterministic policy schedules ready sections, not whole topological layers. Workers
receive copied declared inputs; only the coordinator commits to the caller's mapping.
The existing serial assembler remains a separate, compatible execution choice.

One initial pass may be followed by at most two correction rounds for the entire report.
Failed and flagged sections request repair automatically; skipped and inapplicable ones
do not. A reviewer can identify additional repair roots. A repair invalidates those roots
and every downstream consumer before executing them again. ``repair`` optionally supplies
issue-aware production without changing the Section contract or hiding inputs in context.
"""
from __future__ import annotations

import copy
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional, Protocol, Sequence

from .core.section import FAILED, FLAGGED, OK, SKIPPED, Section, SectionResult, _assemble_one, digest, plan
from .execution_limits import ExecutionLimits, TaskContract
from .isolated import ManagedCall


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    message: str
    repair: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("review issue code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("review issue message must be a non-empty string")
        if (not isinstance(self.repair, tuple)
                or any(not isinstance(key, str) or not key for key in self.repair)):
            raise TypeError("review issue repair must be a tuple of section keys")

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "repair": list(self.repair)}


@dataclass(frozen=True)
class ExecutionResult:
    verdicts: tuple[SectionResult, ...]
    correction_rounds: int
    issues: tuple[ReviewIssue, ...] = ()
    history: tuple[dict, ...] = ()
    limits: Optional[dict] = None
    usage: Optional[dict] = None
    stop_reason: str = ""
    task_states: dict = field(default_factory=dict)
    attempts: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        """Execution metadata only; report values and callable implementations stay out."""
        metadata = {"verdicts": [v.as_dict() for v in self.verdicts],
                    "correction_rounds": self.correction_rounds,
                    "issues": [issue.as_dict() for issue in self.issues],
                    "history": copy.deepcopy(list(self.history))}
        if self.limits is not None:
            metadata.update(limits=copy.deepcopy(self.limits), usage=copy.deepcopy(self.usage),
                            stop_reason=self.stop_reason,
                            task_states=copy.deepcopy(self.task_states),
                            attempts=copy.deepcopy(list(self.attempts)))
        return metadata


class Workflow(Protocol):
    def execute(self, sections: Iterable[Section], result: MutableMapping[str, Any], *,
                inputs: Iterable[str] = (),
                on_section: Optional[Callable[[SectionResult], None]] = None,
                ) -> ExecutionResult: ...


Reviewer = Callable[[dict, tuple[SectionResult, ...]], Sequence[ReviewIssue]]
Repairer = Callable[[Section, dict, tuple[ReviewIssue, ...]], Any]


class DeterministicWorkflow:
    """Bounded parallel dependency scheduling with one report-wide correction budget.

    ``review`` receives isolated result/verdict copies and returns issues. An issue with
    no repair roots remains disclosed without spending correction rounds. Reviewer
    failures become explicit ``review_failed`` issues. Malformed review results are
    configuration errors and raise; an unknown repair root must never be silently ignored.

    ``repair(section, context, issues)`` is called instead of ``section.produce(context)``
    on correction rounds. Section invariants and isolation remain identical. Without the
    adapter, corrections call the original producer. This executor does not fetch sources,
    retry tools internally, or interpret model output as scheduling instructions.
    """

    def __init__(self, max_workers: int = 4, max_correction_rounds: int = 2,
                 review: Optional[Reviewer] = None,
                 repair: Optional[Repairer] = None, *,
                 limits: Optional[ExecutionLimits] = None,
                 contracts: Optional[Mapping[str, TaskContract]] = None) -> None:
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if type(max_correction_rounds) is not int or not 0 <= max_correction_rounds <= 2:
            raise ValueError("max_correction_rounds must be an integer from 0 to 2")
        if review is not None and not callable(review):
            raise TypeError("review must be callable")
        if repair is not None and not callable(repair):
            raise TypeError("repair must be callable")
        if limits is not None and not isinstance(limits, ExecutionLimits):
            raise TypeError("limits must be ExecutionLimits or None")
        if contracts is not None and (not isinstance(contracts, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, TaskContract)
                for key, value in contracts.items())):
            raise TypeError("contracts must map section keys to TaskContract objects")
        if contracts and limits is None:
            raise ValueError("task contracts require bounded execution limits")
        self.max_workers = max_workers
        self.max_correction_rounds = max_correction_rounds
        self.review = review
        self.repair = repair
        self.limits = limits
        self.contracts = dict(contracts or {})

    def execute(self, sections: Iterable[Section], result: MutableMapping[str, Any], *,
                inputs: Iterable[str] = (),
                on_section: Optional[Callable[[SectionResult], None]] = None,
                ) -> ExecutionResult:
        declared = list(sections)
        names = [s.key for s in declared]
        duplicates = sorted({key for key in names if names.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate section output(s): {', '.join(duplicates)}")
        collisions = sorted(set(names) & set(result))
        if collisions:
            raise ValueError("section outputs collide with supplied values: "
                             + ", ".join(collisions))
        ordered = plan(declared)  # Refuses cycles before any producer can run.
        unknown_contracts = set(self.contracts) - set(names)
        if unknown_contracts:
            raise ValueError("contracts name unknown sections: "
                             + ", ".join(sorted(unknown_contracts)))
        if self.limits is not None:
            return _BoundedExecution(self, ordered, result, frozenset(inputs),
                                     on_section).execute()
        keys = set(names)
        dependencies = {s.key: set(s.needs) & keys for s in ordered}
        given = frozenset(inputs)
        verdicts: dict[str, SectionResult] = {}
        # A correction may erase only values this invocation successfully committed.
        owned: dict[str, tuple[str, Optional[str]]] = {}
        history = []
        active = keys
        issues: tuple[ReviewIssue, ...] = ()
        rounds = 0

        while True:
            self._execute_round(ordered, active, dependencies, result, given, verdicts,
                                owned, on_section, issues, correcting=rounds > 0)
            final_verdicts = tuple(verdicts[s.key] for s in ordered)
            issues = self._review(result, final_verdicts, keys)
            history.append({"round": rounds,
                            "executed": [s.key for s in ordered if s.key in active],
                            "issues": [issue.as_dict() for issue in issues],
                            "verdicts": [v.as_dict() for v in final_verdicts]})
            roots = {key for issue in issues for key in issue.repair}
            if not roots or rounds >= self.max_correction_rounds:
                return ExecutionResult(final_verdicts, rounds, issues, tuple(history))
            active = _downstream(roots, dependencies)
            # Check ownership of the complete set before deleting anything.
            for key in active:
                if key in result and (key not in owned or _stamp(result, key) != owned[key]):
                    raise ValueError(f"cannot invalidate {key!r}: output changed outside "
                                     "the workflow or is owned by the caller")
            for key in active:
                if key in result:
                    del result[key]
                owned.pop(key, None)
            rounds += 1

    def _review(self, result: MutableMapping, verdicts: tuple[SectionResult, ...],
                keys: set[str]) -> tuple[ReviewIssue, ...]:
        issues = [ReviewIssue(f"section_{v.status}",
                              f"{v.key}: " + ("; ".join(v.findings) or v.reason),
                              (v.key,))
                  for v in verdicts if v.status in (FAILED, FLAGGED)]
        if self.review is not None:
            try:
                extra = tuple(self.review(copy.deepcopy(dict(result)),
                                          copy.deepcopy(verdicts)))
            except Exception as exc:
                extra = (ReviewIssue("review_failed",
                                     f"{type(exc).__name__}: {exc}"[:300]),)
            for issue in extra:
                if not isinstance(issue, ReviewIssue):
                    raise TypeError("review must return ReviewIssue objects")
                unknown = set(issue.repair) - keys
                if unknown:
                    raise ValueError("review requests unknown section(s): "
                                     + ", ".join(sorted(unknown)))
            issues.extend(extra)
        return tuple(issues)

    def _execute_round(self, ordered: list[Section], active: set[str],
                       dependencies: dict[str, set[str]], result: MutableMapping,
                       given: frozenset[str], verdicts: dict[str, SectionResult],
                       owned: dict[str, tuple[str, Optional[str]]],
                       on_section: Optional[Callable], issues: tuple[ReviewIssue, ...], *,
                       correcting: bool) -> None:
        pending = {s.key: s for s in ordered if s.key in active}
        terminal = set(dependencies) - active
        positions = {s.key: i for i, s in enumerate(ordered)}
        running = {}

        def finish(section: Section, verdict: SectionResult, payload: Any,
                   produced: bool) -> None:
            if produced:
                try:
                    if section.key in result:
                        raise ValueError("refusing to overwrite a value supplied during execution")
                    result[section.key] = payload
                    owned[section.key] = _stamp(result, section.key)
                except Exception as exc:
                    verdict = replace(verdict, status=FAILED,
                                      reason=f"commit failed: {type(exc).__name__}: {exc}"[:300])
            verdicts[section.key] = verdict
            terminal.add(section.key)
            if on_section is not None:
                try:
                    on_section(copy.deepcopy(verdict))
                except Exception:
                    pass  # Progress delivery is independent of producing the report.

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while pending or running:
                for key, section in list(pending.items()):
                    if len(running) >= self.max_workers:
                        break
                    if not dependencies[key] <= terminal:
                        continue
                    del pending[key]
                    try:
                        snapshot = {name: copy.deepcopy(result[name])
                                    for name in section.needs if name in result}
                    except Exception as exc:
                        finish(section, SectionResult(key, FAILED,
                               f"input snapshot failed: {type(exc).__name__}: {exc}"[:300],
                               label=section.label), None, False)
                        continue
                    producer = section
                    if correcting and self.repair is not None:
                        producer = replace(section, produce=lambda context, s=section:
                                           self.repair(s, context, issues))
                    future = pool.submit(_work, producer, snapshot, given)
                    running[future] = section
                if not running:
                    if pending:
                        raise RuntimeError("workflow stalled despite an acyclic dependency plan")
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda f: positions[running[f].key]):
                    section = running.pop(future)
                    try:
                        verdict, payload, produced = future.result()
                    except Exception as exc:
                        verdict = SectionResult(section.key, FAILED,
                                                f"{type(exc).__name__}: {exc}"[:300],
                                                label=section.label)
                        payload, produced = None, False
                    finish(section, verdict, payload, produced)


def _work(section: Section, snapshot: dict, given: frozenset[str]) -> tuple:
    """The existing section checks run against a private mapping in a worker."""
    verdict = _assemble_one(section, snapshot, given)
    produced = section.key in snapshot
    return verdict, copy.deepcopy(snapshot.get(section.key)), produced


def _stamp(result: MutableMapping, key: str) -> tuple[str, Optional[str]]:
    # FactStore metadata detects stronger replacements even when the value is unchanged.
    fact = getattr(result, "fact", None)
    return digest(result[key]), digest(fact(key).as_dict()) if callable(fact) else None


def _downstream(roots: set[str], dependencies: dict[str, set[str]]) -> set[str]:
    affected = set(roots)
    while True:
        more = {key for key, needs in dependencies.items() if needs & affected} - affected
        if not more:
            return affected
        affected.update(more)


class _BudgetLedger:
    """The coordinator owns leases; untrusted or missing receipts never refund them."""

    def __init__(self, limits: ExecutionLimits) -> None:
        self.maximum = {"model_calls": limits.max_model_calls,
                        "tool_calls": limits.max_tool_calls}
        self.available = dict(self.maximum)
        self.reserved = dict.fromkeys(self.maximum, 0)
        self.actual = dict.fromkeys(self.maximum, 0)
        self.charged = dict.fromkeys(self.maximum, 0)
        self.uncertain = 0

    def lease(self, model_calls: int, tool_calls: int) -> dict:
        allocation = {"model_calls": min(model_calls, self.available["model_calls"]),
                      "tool_calls": min(tool_calls, self.available["tool_calls"])}
        for key, count in allocation.items():
            self.available[key] -= count
            self.reserved[key] += count
        return allocation

    def waiting_for_refund(self, model_calls: int, tool_calls: int) -> bool:
        requested = {"model_calls": model_calls, "tool_calls": tool_calls}
        return any(self.available[key] < requested[key] and self.reserved[key]
                   for key in requested)

    def settle(self, allocation: dict, receipt: Any) -> dict:
        valid = receipt is not None and receipt.usage_valid and isinstance(receipt.usage, dict)
        if valid:
            valid = all(type(receipt.usage.get(key)) is int
                        and 0 <= receipt.usage[key] <= count
                        for key, count in allocation.items())
        charge = dict(receipt.usage) if valid else dict(allocation)
        for key, count in allocation.items():
            self.reserved[key] -= count
            self.available[key] += count - charge[key]
            self.charged[key] += charge[key]
            if valid:
                self.actual[key] += charge[key]
        if not valid:
            self.uncertain += 1
        return charge

    def as_dict(self) -> dict:
        return {"actual": dict(self.actual), "charged": dict(self.charged),
                "remaining": dict(self.available), "uncertain_attempts": self.uncertain}


class _BoundedExecution:
    """One finite process-controlled run; neither workers nor reviewers schedule work.

    Only finite JSON enters the result mapping. Flagged and malformed outputs are held
    back, so a dependent cannot consume them before a successful correction. Producer,
    applicability, contract and invariant code all share the task's process deadline.
    Progress callbacks run after each round's workers settle, in separate processes with
    at most one second each. Delivery cannot delay worker polling. Callback side effects
    in parent memory therefore do not propagate in this execution mode.
    """

    def __init__(self, workflow: DeterministicWorkflow, ordered: list[Section],
                 result: MutableMapping, given: frozenset[str],
                 on_section: Optional[Callable]) -> None:
        self.workflow = workflow
        self.limits = workflow.limits
        self.ordered = ordered
        self.result = result
        self.given = given
        self.on_section = on_section
        self.keys = {section.key for section in ordered}
        self.dependencies = {section.key: set(section.needs) & self.keys
                             for section in ordered}
        self.verdicts: dict[str, SectionResult] = {}
        self.owned: dict[str, tuple[str, Optional[str]]] = {}
        self.ledger = _BudgetLedger(self.limits)
        self.deadline = time.monotonic() + self.limits.run_timeout_s
        self.history: list[dict] = []
        self.attempts: list[dict] = []
        self.progress: list[dict] = []
        self.notifications: list[SectionResult] = []
        self.stop_reason = ""
        self.abort_issue: Optional[ReviewIssue] = None
        self.round = 0

    def execute(self) -> ExecutionResult:
        active = set(self.keys)
        issues: tuple[ReviewIssue, ...] = ()
        while True:
            self._execute_round(active, issues)
            self._flush_progress()
            verdicts = tuple(self.verdicts[section.key] for section in self.ordered)
            issues = self._review(verdicts)
            self.history.append({"round": self.round,
                                 "executed": [s.key for s in self.ordered if s.key in active],
                                 "issues": [issue.as_dict() for issue in issues],
                                 "verdicts": [v.as_dict() for v in verdicts]})
            roots = {key for issue in issues for key in issue.repair}
            if self.abort_issue is not None:
                break
            if not roots:
                self.stop_reason = "unresolved_issues" if issues else "completed"
                break
            if self.round >= self.workflow.max_correction_rounds:
                self.stop_reason = "correction_limit"
                break
            if time.monotonic() >= self.deadline:
                self._abort("run_deadline", "Report execution deadline exceeded")
                issues += (self.abort_issue,)
                break
            active = _downstream(roots, self.dependencies)
            for key in active:
                if key in self.result and (key not in self.owned
                                          or _stamp(self.result, key) != self.owned[key]):
                    raise ValueError(f"cannot invalidate {key!r}: output changed outside "
                                     "the workflow or is owned by the caller")
            for key in active:
                if key in self.result:
                    del self.result[key]
                self.owned.pop(key, None)
            self.round += 1
        states = {verdict.key: {"status": verdict.status,
                               "attempts": sum(attempt["section"] == verdict.key
                                               for attempt in self.attempts)}
                  for verdict in verdicts}
        usage = self.ledger.as_dict()
        usage["progress_callbacks"] = list(self.progress)
        return ExecutionResult(verdicts, self.round, issues, tuple(self.history),
                               asdict(self.limits), usage, self.stop_reason, states,
                               tuple(self.attempts))

    def _abort(self, code: str, message: str) -> None:
        if self.abort_issue is None:
            self.stop_reason = code
            self.abort_issue = ReviewIssue(code, message)

    def _finish(self, section: Section, verdict: SectionResult,
                payload: Any = None, produced: bool = False) -> None:
        if produced and time.monotonic() >= self.deadline:
            self._abort("run_deadline", "Report execution deadline exceeded")
            verdict = replace(verdict, status=FAILED, reason="run_deadline")
            produced = False
        if produced and self.abort_issue is not None:
            verdict = replace(verdict, status=FAILED,
                              reason=f"workflow stopped: {self.stop_reason}")
            produced = False
        if produced and verdict.status == OK:
            written = False
            try:
                if section.key in self.result:
                    raise ValueError("refusing to overwrite supplied value")
                self.result[section.key] = payload
                written = True
                self.owned[section.key] = _stamp(self.result, section.key)
            except Exception as exc:
                if written:
                    # A failed metadata commit must not leave an apparently valid value.
                    self.result.pop(section.key, None)
                verdict = replace(verdict, status=FAILED,
                                  reason=f"commit failed: {type(exc).__name__}")
        self.verdicts[section.key] = verdict
        if self.on_section is not None:
            self.notifications.append(verdict)

    def _flush_progress(self) -> None:
        notifications, self.notifications = self.notifications, []
        for verdict in notifications:
            self._progress(verdict)

    def _progress(self, verdict: SectionResult) -> None:
        if (self.on_section is None or self.abort_issue is not None
                or time.monotonic() >= self.deadline):
            return
        call = None
        try:
            call = ManagedCall.start(_bounded_progress,
                                     args=(self.on_section, verdict.as_dict()),
                                     deadline=min(self.deadline, time.monotonic() + 1),
                                     model_calls=0, tool_calls=0)
            receipt = self._wait(call)
            self.progress.append({"section": verdict.key, "status": receipt.status})
        except Exception as exc:
            self.progress.append({"section": verdict.key, "status": "error",
                                  "error": type(exc).__name__})
        finally:
            if call is not None:
                call.close()

    def _execute_round(self, active: set[str], issues: tuple[ReviewIssue, ...]) -> None:
        pending = {section.key: section for section in self.ordered if section.key in active}
        terminal = self.keys - active
        running: dict[str, tuple] = {}
        try:
            while pending or running:
                if time.monotonic() >= self.deadline:
                    self._abort("run_deadline", "Report execution deadline exceeded")
                if self.abort_issue is not None:
                    for key, (section, call, allocation) in list(running.items()):
                        receipt = call.terminate(self.stop_reason)
                        self._record(section.key, receipt, allocation)
                        call.close()
                        self._finish(section, SectionResult(key, FAILED, self.stop_reason,
                                                           label=section.label))
                    running.clear()
                    for section in pending.values():
                        self._finish(section, SectionResult(section.key, SKIPPED,
                                     f"workflow stopped: {self.stop_reason}", label=section.label))
                    pending.clear()
                    break
                for key, section in list(pending.items()):
                    if len(running) >= self.workflow.max_workers:
                        break
                    if not self.dependencies[key] <= terminal:
                        continue
                    if running and self.ledger.waiting_for_refund(
                            self.limits.task_model_calls, self.limits.task_tool_calls):
                        break
                    del pending[key]
                    allocation = self.ledger.lease(self.limits.task_model_calls,
                                                   self.limits.task_tool_calls)
                    try:
                        snapshot = _json_copy({name: self.result[name]
                            for name in section.needs if name in self.result
                            and (name not in self.keys or self.verdicts[name].status == OK)})
                        call = ManagedCall.start(
                            _bounded_work,
                            args=(section, snapshot, self.given,
                                  self.workflow.contracts.get(key),
                                  self.workflow.repair if self.round else None, issues),
                            deadline=min(self.deadline,
                                         time.monotonic() + self.limits.task_timeout_s),
                            **allocation)
                    except Exception as exc:
                        self._record(key, None, allocation, error=type(exc).__name__)
                        self._finish(section, SectionResult(key, FAILED,
                                     f"worker launch failed: {type(exc).__name__}",
                                     label=section.label))
                        terminal.add(key)
                        continue
                    running[key] = (section, call, allocation)
                completed = []
                for key, (section, call, allocation) in list(running.items()):
                    receipt = call.poll()
                    if receipt is None:
                        continue
                    completed.append(key)
                    del running[key]
                    self._record(key, receipt, allocation)
                    call.close()
                    verdict, payload, produced = self._decode_task(section, receipt)
                    self._finish(section, verdict, payload, produced)
                    terminal.add(key)
                if pending and not running and not completed:
                    if not any(self.dependencies[key] <= terminal for key in pending):
                        raise RuntimeError("workflow stalled despite acyclic dependencies")
                if running and not completed:
                    time.sleep(min(0.01, max(0, self.deadline - time.monotonic())))
        finally:
            # Unexpected coordinator failures must not leave workers running or leased.
            for key, (_section, call, allocation) in running.items():
                receipt = call.terminate("coordinator_stopped")
                self._record(key, receipt, allocation)
                call.close()

    def _decode_task(self, section: Section, receipt: Any) -> tuple:
        if receipt.status == "timeout":
            code = "run_deadline" if time.monotonic() >= self.deadline else "task_deadline"
            self._abort(code, f"Execution deadline exceeded while running {section.key}")
        elif receipt.status == "budget_exceeded":
            self._abort("call_budget", f"Model or tool call budget exceeded in {section.key}")
        if receipt.status != "ok":
            return (SectionResult(section.key, FAILED,
                                  f"worker {receipt.status}: {receipt.error or receipt.status}",
                                  label=section.label), None, False)
        value = receipt.value
        try:
            if not isinstance(value, dict) or set(value) != {"verdict", "payload", "produced"}:
                raise ValueError("invalid worker result")
            verdict = SectionResult(**value["verdict"])
            if verdict.key != section.key or type(value["produced"]) is not bool:
                raise ValueError("invalid worker identity")
            return verdict, value["payload"], value["produced"] and verdict.status == OK
        except (TypeError, ValueError, KeyError):
            return SectionResult(section.key, FAILED, "invalid worker result",
                                 label=section.label), None, False

    def _record(self, section: str, receipt: Any, allocation: dict, error: str = "") -> None:
        charged = self.ledger.settle(allocation, receipt)
        self.attempts.append({"section": section, "round": self.round,
                              "status": receipt.status if receipt else "launch_failed",
                              "pid": receipt.pid if receipt else None,
                              "elapsed_s": receipt.elapsed if receipt else 0,
                              "lease": dict(allocation), "charged": charged,
                              "usage": receipt.usage if receipt and receipt.usage_valid else None,
                              "usage_valid": bool(receipt and receipt.usage_valid),
                              "llm_usage": receipt.llm_usage if receipt else {},
                              "error": receipt.error if receipt else error})

    def _wait(self, call: Any) -> Any:
        while True:
            if time.monotonic() >= self.deadline:
                return call.terminate("run_deadline")
            receipt = call.poll()
            if receipt is not None:
                return receipt
            time.sleep(min(0.01, max(0, self.deadline - time.monotonic())))

    def _review(self, verdicts: tuple[SectionResult, ...]) -> tuple[ReviewIssue, ...]:
        issues = [ReviewIssue(f"section_{verdict.status}",
                              f"{verdict.key}: " + ("; ".join(verdict.findings) or verdict.reason),
                              (verdict.key,) if verdict.status in (FAILED, FLAGGED) else ())
                  for verdict in verdicts if verdict.status in (FAILED, FLAGGED, SKIPPED)]
        if self.abort_issue is not None:
            return tuple(issues + [self.abort_issue])
        if time.monotonic() >= self.deadline:
            self._abort("run_deadline", "Report execution deadline exceeded before review")
            return tuple(issues + [self.abort_issue])
        if self.workflow.review is None:
            return tuple(issues)
        allocation = self.ledger.lease(self.limits.review_model_calls, 0)
        call = None
        receipt = None
        error = ""
        try:
            call = ManagedCall.start(_bounded_review,
                                     args=(self.workflow.review, _json_copy(dict(self.result)),
                                           [v.as_dict() for v in verdicts], self.keys),
                                     deadline=min(self.deadline, time.monotonic()
                                                  + self.limits.review_timeout_s),
                                     **allocation)
            receipt = self._wait(call)
        except Exception as exc:
            error = type(exc).__name__
        finally:
            self._record("__review__", receipt, allocation, error)
            if call is not None:
                call.close()
        if receipt is None or receipt.status != "ok":
            status = receipt.status if receipt else "launch_failed"
            code = ("run_deadline" if time.monotonic() >= self.deadline else
                    "review_deadline" if status == "timeout" else
                    "call_budget" if status == "budget_exceeded" else "review_failed")
            self._abort(code, f"Report review did not complete: {status}")
            return tuple(issues + [self.abort_issue])
        try:
            issues.extend(ReviewIssue(item["code"], item["message"], tuple(item["repair"]))
                          for item in receipt.value)
        except (TypeError, ValueError, KeyError):
            self._abort("review_failed", "Report review returned invalid issues")
            issues.append(self.abort_issue)
        return tuple(issues)


def _json_copy(value: Any) -> Any:
    """Bounded execution accepts finite JSON snapshots, never arbitrary object graphs."""
    return json.loads(json.dumps(value, allow_nan=False))


def _bounded_work(section: Section, snapshot: dict, given: frozenset[str],
                  contract: Optional[TaskContract], repair: Optional[Repairer],
                  issues: tuple[ReviewIssue, ...]) -> dict:
    def produce(context: dict) -> Any:
        if contract is not None and contract.input_validator is not None:
            _validate_contract(contract.input_validator, context, "input")
        try:
            payload = repair(section, context, issues) if repair else section.produce(context)
        except Exception as exc:
            raise RuntimeError(f"producer raised {type(exc).__name__}") from None
        if contract is not None and contract.output_validator is not None:
            _validate_contract(contract.output_validator, payload, "output")
        return payload

    safe_invariants = tuple((name, _bounded_invariant(check))
                            for name, check in section.invariants)
    verdict = _assemble_one(replace(section, produce=produce, invariants=safe_invariants),
                            snapshot, given)
    produced = verdict.status == OK and section.key in snapshot
    return {"verdict": verdict.as_dict(), "payload": snapshot.get(section.key) if produced else None,
            "produced": produced}


def _bounded_invariant(check: Callable) -> Callable:
    """Keep normal findings, but never expose exception text from a detector."""
    def inspect(payload: Any) -> str:
        try:
            finding = check(payload)
            return str(finding) if finding else ""
        except Exception as exc:
            return f"detector raised {type(exc).__name__}"
    return inspect


def _validate_contract(validator: Callable, value: Any, direction: str) -> None:
    try:
        answer = validator(copy.deepcopy(value))
    except Exception as exc:
        raise ValueError(f"{direction} contract failed: {type(exc).__name__}") from None
    if answer is not None:
        raise ValueError(f"{direction} contract validator must return None or raise")


def _bounded_review(review: Reviewer, result: dict, verdicts: list[dict], keys: set[str]) -> list:
    issues = tuple(review(result, tuple(SectionResult(**verdict) for verdict in verdicts)))
    for issue in issues:
        if not isinstance(issue, ReviewIssue):
            raise TypeError("review must return ReviewIssue objects")
        if set(issue.repair) - keys:
            raise ValueError("review requests unknown sections")
    return [issue.as_dict() for issue in issues]


def _bounded_progress(callback: Callable, verdict: dict) -> None:
    callback(SectionResult(**verdict))
