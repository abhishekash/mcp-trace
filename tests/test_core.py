"""Core query tests against a real agent-harness trace fixture."""
import pytest

from mcp_trace import core

TRACE_ID = "f920798dd2558fcb84f9977736432a8b"


@pytest.fixture(scope="module")
def spans():
    return core.load_spans("tests/fixtures/demo_trace.jsonl")


@pytest.fixture(scope="module")
def trace_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("traces")
    import shutil

    shutil.copy("tests/fixtures/demo_trace.jsonl", d / "demo.jsonl")
    return d


class TestLoading:
    def test_load_spans(self, spans):
        assert len(spans) == 15
        assert {s["trace_id"] for s in spans} == {TRACE_ID}

    def test_load_trace_dir(self, trace_dir):
        assert len(core.load_trace_dir(trace_dir)) == 15

    def test_load_trace_dir_recursive(self, trace_dir):
        nested = trace_dir / "2026" / "04"
        nested.mkdir(parents=True)
        import shutil

        shutil.copy("tests/fixtures/demo_trace.jsonl", nested / "nested.jsonl")
        assert len(core.load_trace_dir(trace_dir)) == 30

    def test_missing_dir(self):
        with pytest.raises(FileNotFoundError):
            core.load_trace_dir("/nope/not-here")

    def test_group_traces_sorted(self, spans):
        traces = core.group_traces(spans)
        starts = [s["start_unix_nano"] for s in traces[TRACE_ID]]
        assert starts == sorted(starts)


class TestRunSummary:
    def test_summary_fields(self, spans):
        s = core.run_summary(TRACE_ID, spans)
        assert s["task"].startswith("Summarize the workspace's notes")
        assert s["model"] == "scripted"
        assert s["tool_calls"] == 4
        assert s["tools_used"] == ["list_dir", "read_file", "run_shell", "write_file"]
        assert s["human_decisions"] == 2
        assert s["denials"] == 0
        assert s["stopped_reason"] == "completed"

    def test_list_runs(self, spans):
        runs = core.list_runs(spans)
        assert len(runs) == 1
        assert runs[0]["trace_id"] == TRACE_ID


class TestSpanTree:
    def test_shape(self, spans):
        tree = core.span_tree(TRACE_ID, spans)
        assert tree["name"] == "agent.run"
        steps = tree["children"]
        assert len(steps) == 5
        assert all(s["name"] == "agent.step" for s in steps)
        # step 3 holds the write_file call with its hitl event
        step3_children = steps[2]["children"]
        tool_span = next(c for c in step3_children if c["name"] == "tool.call")
        assert tool_span["attributes"]["tool.name"] == "write_file"
        assert "hitl.decision" in tool_span["events"]


class TestSlowest:
    def test_slowest_first(self, spans):
        top = core.slowest_spans(spans, k=2)
        assert top[0]["name"] == "agent.run"
        assert top[0]["duration_ms"] >= top[1]["duration_ms"]

    def test_filter_by_name(self, spans):
        top = core.slowest_spans(spans, k=5, name_filter="tool")
        assert all("tool" in t["name"] for t in top)
        assert top[0]["tool"] == "run_shell"  # the slowest tool call in the demo


class TestApprovalLog:
    def test_decisions_extracted(self, spans):
        log = core.approval_log(spans)
        assert len(log) == 2
        assert {entry["tool"] for entry in log} == {"write_file", "run_shell"}
        assert all(entry["decision"] == "approve" for entry in log)
        assert all(entry["approver"] == "auto" for entry in log)


class TestTokenUsage:
    def test_aggregation(self, spans):
        usage = core.token_usage(spans)
        assert usage["llm_calls"] == 5
        assert usage["input_tokens"] == 92 + 94 + 125 + 132 + 196
        assert usage["output_tokens"] > 0


class TestNewQueries:
    def test_failure_report_is_bounded_and_redacted(self, spans):
        failed = dict(spans[1])
        failed["attributes"] = {
            **failed["attributes"],
            "tool.result_status": "error",
            "tool.error_message": "authorization: Bearer sk-12345678901234567890",
        }
        report = core.failure_report(spans + [failed], limit=1)
        assert len(report) == 1
        assert report[0]["result_status"] == "error"
        assert "sk-123" not in report[0]["detail"]
        assert "<redacted>" in report[0]["detail"]

    def test_tool_stats(self, spans):
        stats = core.tool_stats(spans)
        assert len(stats) == 4
        shell = next(row for row in stats if row["tool"] == "run_shell")
        assert shell["calls"] == 1
        assert shell["errors"] == 0
        assert shell["p95_duration_ms"] > 0

    def test_security_audit_flags_missing_provenance(self, spans):
        audit = core.security_audit(spans)
        assert audit["calls"] == 4
        assert audit["missing_identity"] == 4
        assert audit["missing_server_provenance"] == 4
        assert audit["records"][0]["identity"] is None

    def test_recent_activity_uses_cursor(self, spans):
        first = core.recent_activity(spans, limit=2)
        second = core.recent_activity(spans, cursor=first["next_cursor"], limit=2)
        assert first["count"] == 2
        assert second["count"] == 2
        assert not {row["span_id"] for row in first["spans"]} & {
            row["span_id"] for row in second["spans"]
        }

    def test_compare_runs(self, spans):
        compared = core.compare_runs(spans, [TRACE_ID, TRACE_ID])
        assert len(compared["runs"]) == 2
        assert compared["runs"][1]["delta_vs_first"]["total_tokens"] == 0


class TestSearch:
    def test_by_tool_name(self, spans):
        hits = core.search_spans(spans, "write_file")
        assert hits and all("write_file" in str(h["matched"]) for h in hits)

    def test_no_hits(self, spans):
        assert core.search_spans(spans, "definitely-not-here") == []
