"""The workspace the Brain explores -- documents, a schema map, an example bank.

v3's whole premise: stop pre-digesting context into lossy machine formats (embeddings,
compiled catalogs, synthesized briefs) and instead give the agent a workspace of raw,
readable material it navigates itself. Three things, all lossless:

  - schema map: tables -> columns -> descriptions, plus join edges OBSERVED in real SQL
  - example bank: gold NL-SQL pairs + query history, indexed by the tables/columns their
    SQL actually uses (exact, from parsing the SQL -- not a fuzzy embedding of the question)
  - docs: authored prose (metric definitions, domain notes, SQL idioms) the agent reads

Retrieval over examples keys on STRUCTURE (which tables/columns the SQL touches) because
the SQL is unambiguous; question text is only a secondary keyword signal. This is the fix
for the failure we debugged in v2, where a fuzzy question-embedding buried the exact gold
pair under a 0.048-scored unrelated pattern.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from diracdata.config import Config
from diracdata.utils.sql import analyze_sql_references

_DEFAULTS = Config()
_TOKEN = re.compile(r"[a-z0-9_]+")

# Common filler words that carry no relevance signal. This is a light retrieval aid ONLY:
# find_examples returns broad candidates and the cognition AGENT judges which are relevant.
# Retrieval never decides on its own -- it just avoids handing the agent obvious noise.
_STOPWORDS = frozenset(
    """how many what which who when where why is are was were be been the a an of in on at to for with by
    from and or not no than more less most least have has had do does did per that this these those it as
    all each any some there here their they we you your our show list count number total give find return
    get me my within over under between during whose""".split()
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if len(t) > 1 and t not in _STOPWORDS}


def _normalize_question(text: str) -> str:
    return " ".join((text or "").lower().split()).rstrip(".?! ")


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


@dataclass(frozen=True)
class Example:
    source: str  # "gold" | "history"
    question: str  # NL question (empty for history)
    sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]

    def _haystack(self) -> set[str]:
        # Structure (tables/columns the SQL uses) + the question words. Structure is the
        # exact key; question words are the secondary signal.
        toks = set(self.tables)
        for col in self.columns:
            toks.update(col.replace(".", " ").split())
        toks |= _tokens(self.question)
        return toks


@dataclass
class Workspace:
    metadata: dict[str, Any]
    examples: list[Example] = field(default_factory=list)
    docs: dict[str, str] = field(default_factory=dict)
    value_domains: dict[str, Any] = field(default_factory=dict)
    semantic_layer: dict[str, Any] = field(default_factory=dict)
    _join_edges: dict[str, set[str]] = field(default_factory=dict)
    _table_columns: dict[str, list[str]] = field(default_factory=dict)
    # graph join edges: table -> list of (col, target_table, target_col, source)
    _graph: dict[str, list[tuple]] = field(default_factory=lambda: defaultdict(list))
    _known_edges: set = field(default_factory=set)  # normalized "a.c = b.d" (undirected)

    # ---- construction -----------------------------------------------------------------
    @classmethod
    def from_store(cls, *, store: Any, schema: str, **kwargs: Any) -> "Workspace":
        """Build a workspace from the object-store domain context: the learning agent's metadata,
        value domains, join graph AND the customer's business-definitions/metrics layer all come
        from the store. V3-S4: also loads `gold_pairs.json` (a list of {nl_query, sql} seeds) from
        the same store, so find_examples returns proven patterns even on cold schemas (no local
        files needed). Additional local files can still pass through **kwargs."""
        return cls.load(
            metadata=store.get(schema, "metadata_descriptions.json") or {},
            value_domains=store.get(schema, "value_domains.json") or {},
            join_graph=store.get(schema, "join_graph.json") or [],
            semantic_layer=_semantic_layer_from_store(store, schema),
            gold_pairs=store.get(schema, "gold_pairs.json") or [],
            **kwargs,
        )

    @classmethod
    def from_catalog_store(cls, *, catalog_store: Any, catalog: str, database: str,
                          **kwargs: Any) -> "Workspace":
        """Build a workspace from the catalog-level fabric (catalog+database scope).

        Reads the same artifact set as `from_store` but goes through CatalogStore, which
        prefers the new fabric/catalogs/<catalog>/databases/<database>/ layout and falls
        back to legacy fabric/<database>/ automatically for catalog='local' (Decision #3).

        Also loads the freshly-authored database.md index if present so callers that
        want to inject it into framing prompts can do so via `ws.database_md`.
        """
        metadata     = catalog_store.get(catalog, database, "metadata_descriptions.json") or {}
        value_dom    = catalog_store.get(catalog, database, "value_domains.json") or {}
        join_graph   = catalog_store.get(catalog, database, "join_graph.json") or []
        gold_pairs   = catalog_store.get(catalog, database, "gold_pairs.json") or []
        # Semantic layer: legacy _semantic_layer_from_store expects a schema-shaped
        # store.read_text(schema, name) interface. CatalogStore exposes get_text(catalog, db, name)
        # so we adapt inline (small, no need for a wrapper class):
        sl_text = catalog_store.get_text(catalog, database, "semantic_layer.yaml")
        semantic_layer = _parse_semantic_layer_yaml(sl_text) if sl_text else {}
        ws = cls.load(
            metadata=metadata, value_domains=value_dom, join_graph=join_graph,
            semantic_layer=semantic_layer, gold_pairs=gold_pairs, **kwargs,
        )
        # Attach optional catalog-authored indexes so downstream framing can inject them.
        ws.catalog  = catalog
        ws.database = database
        ws.database_md = catalog_store.get_text(catalog, database, "database.md") or ""
        ws.catalog_md  = catalog_store.get_catalog_text(catalog, "catalog.md") or ""
        return ws

    @classmethod
    def load(
        cls,
        *,
        metadata_path: Path | None = None,
        metadata: dict | None = None,
        gold_pairs_path: Path | None = None,
        gold_pairs: list | None = None,        # V3-S4: pre-loaded seeds from the object store
        query_history_path: Path | None = None,
        docs_paths: list[Path] | None = None,
        value_domains_path: Path | None = None,
        value_domains: dict | None = None,
        join_graph: list | None = None,
        semantic_layer_path: Path | None = None,
        semantic_layer: dict | None = None,
    ) -> "Workspace":
        # Generated artifacts may arrive as dicts (from the object store) or paths (tests/source).
        if metadata is None:
            metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        table_columns = {t: list(cols) for t, cols in (metadata.get("columns") or {}).items()}
        ws = cls(metadata=metadata)
        ws._table_columns = table_columns
        if value_domains is not None:
            ws.value_domains = value_domains
        elif value_domains_path and Path(value_domains_path).exists():
            ws.value_domains = json.loads(Path(value_domains_path).read_text(encoding="utf-8"))
        # Join graph: convention edges plus the learning agent's VERIFIED join graph (Phase 2).
        # BFS composes single-hop edges into 3-/4-way paths.
        for left_t, left_c, right_t, right_c in _derive_join_edges(table_columns):
            ws._add_edge(left_t, left_c, right_t, right_c, "derived")
        for rec in join_graph or []:
            ws._add_edge(rec["left_table"], rec["left_col"], rec["right_table"], rec["right_col"], "compiled")

        if gold_pairs_path and Path(gold_pairs_path).exists():
            for row in csv.DictReader(Path(gold_pairs_path).open(encoding="utf-8")):
                ws._add_example("gold", row.get("nl_query", ""), row.get("sql", ""), table_columns)
        if gold_pairs:                        # V3-S4: seeds from the object store (gold_pairs.json)
            for row in gold_pairs:
                if isinstance(row, dict):
                    ws._add_example("gold", row.get("nl_query", "") or row.get("question", ""),
                                    row.get("sql", ""), table_columns)
        if query_history_path and Path(query_history_path).exists():
            for row in csv.DictReader(Path(query_history_path).open(encoding="utf-8")):
                ws._add_example("history", "", row.get("statement_text", ""), table_columns)

        if semantic_layer is not None:                       # from the object-store domain context
            ws.semantic_layer = _normalize_semantic_layer(semantic_layer) or {}
        elif semantic_layer_path and Path(semantic_layer_path).exists():
            import yaml
            ws.semantic_layer = _normalize_semantic_layer(
                yaml.safe_load(Path(semantic_layer_path).read_text(encoding="utf-8")) or {}) or {}

        for doc_path in docs_paths or []:
            p = Path(doc_path)
            if p.exists():
                ws.docs[p.name] = p.read_text(encoding="utf-8")
        return ws

    # ---- semantic layer (customer-configured metrics/terms/dimensions) ----------------
    def definitions_index(self) -> str:
        """A compact index of defined business terms + metrics, for cognition to bind to."""
        sl = self.semantic_layer
        if not sl:
            return ""
        lines = []
        terms = sl.get("business_terms") or {}
        if terms:
            lines.append("DEFINED BUSINESS TERMS (use these definitions verbatim; call `define` for the full text):")
            for name, body in terms.items():
                lines.append(f"  - {name}: {_one_line((body or {}).get('description'))}")
        metrics = sl.get("metrics") or {}
        if metrics:
            lines.append("DEFINED METRICS (a tree; depends_on = drivers to decompose a change into, "
                         "multiplicative or additive; call `define` for the SQL/formula):")
            for name, body in metrics.items():
                d = _one_line((body or {}).get("description"))
                deps = (body or {}).get("depends_on") or []
                extra = f"  -> depends_on ({(body or {}).get('decomposition','')}): {', '.join(deps)}" if deps else ""
                lines.append(f"  - {name}: {d}{extra}")
        dims = sl.get("dimensions") or {}
        if dims:
            lines.append("ATTRIBUTION DIMENSIONS (the blessed slices to break a metric down by / attribute a "
                         "change into -- bind a user's phrasing to one of THESE names rather than guessing a "
                         "raw column, then pass them to `attribute`; [primary] = attributed by default, "
                         "group = a family you can request as a set):")
            for name, body in dims.items():
                b = body or {}
                tags = " ".join(t for t in ("[primary]" if b.get("primary") else "",
                                            f"group={b.get('group')}" if b.get("group") else "") if t)
                lines.append(f"  - {name}: {_one_line(b.get('description'))}" + (f"  ({tags})" if tags else ""))
        channels = sl.get("channels") or {}
        if channels:
            lines.append("SALES-CHANNEL FACT TABLES (key columns DIFFER per channel -- use THESE exact "
                         "names, do not assume one channel's columns for another):")
            for name, body in channels.items():
                cols = ", ".join(f"{role}={col}" for role, col in (body or {}).items() if role != "fact")
                lines.append(f"  - {name} ({(body or {}).get('fact')}): {cols}")
        return "\n".join(lines)

    def define(self, name: str) -> str:
        """Full definition of a business term or metric, with its SQL / formula."""
        sl = self.semantic_layer
        key = _normalize_question(name).replace(" ", "_")
        for section in ("business_terms", "metrics", "dimensions"):
            body = (sl.get(section) or {}).get(name) or (sl.get(section) or {}).get(key)
            if body is None:
                continue
            if isinstance(body, str):
                return f"{name}: {body}"
            parts = [f"{name} ({section[:-1]})"]
            for field_name in ("description", "definition", "sql", "formula", "depends_on", "decomposition",
                               "grain", "dimensions"):
                v = body.get(field_name)
                if v:
                    parts.append(f"  {field_name}: {v if not isinstance(v, list) else ', '.join(map(str, v))}")
            return "\n".join(parts)
        names = sorted(
            n for section in ("business_terms", "metrics", "dimensions")
            for n in (sl.get(section) or {})
        )
        avail = f" Defined names are: {', '.join(names)}." if names else ""
        return (f"No definition found for '{name}'.{avail} "
                "If what you need isn't a defined term, use describe_table for the raw schema.")

    def metric(self, name: str) -> dict | None:
        """The RAW metric definition (dict) for a name -- the structured counterpart to define().
        None if the name isn't a defined metric."""
        metrics = (self.semantic_layer or {}).get("metrics") or {}
        key = _normalize_question(name).replace(" ", "_")
        body = metrics.get(name) or metrics.get(key)
        return body if isinstance(body, dict) else None

    def metric_tree(self, name: str, *, max_depth: int = _DEFAULTS.metric_tree_max_depth) -> dict:
        """The recursive driver decomposition of a metric, so RCA is a systematic WALK rather than
        N separate define() calls. Returns a nested node::

            {name, defined, description?, sql?/formula?, decomposition?, grain?,
             drivers?: [child node, ...] | drivers_truncated?: [name, ...]}

        Structure only (measured/authored) -- quantifying and ranking the drivers stays the agent's
        job. Cycle- and depth-safe: a driver that recurses back onto an ancestor, or sits past
        max_depth, is listed under `drivers_truncated` instead of expanded. A driver name that isn't a
        defined metric becomes a leaf {name, defined: False} (still nameable in a fan-out)."""
        def build(nm: str, depth: int, path: frozenset) -> dict:
            body = self.metric(nm)
            if body is None:
                return {"name": nm, "defined": False}
            node: dict[str, Any] = {"name": nm, "defined": True}
            for f in ("description", "sql", "formula", "decomposition", "grain"):
                if body.get(f):
                    node[f] = body[f]
            deps = list(body.get("depends_on") or [])
            if not deps:
                return node
            if depth >= max_depth or nm in path:            # bound / cycle -> name the drivers, don't expand
                node["drivers_truncated"] = deps
            else:
                node["drivers"] = [build(d, depth + 1, path | {nm}) for d in deps]
            return node
        return build(name, 0, frozenset())

    # ---- join graph -------------------------------------------------------------------
    def _add_edge(self, lt: str, lc: str, rt: str, rc: str, source: str) -> None:
        cond = _edge_key(f"{lt}.{lc}", f"{rt}.{rc}")
        if cond in self._known_edges:
            return
        self._known_edges.add(cond)
        self._graph[lt].append((lc, rt, rc, source))
        self._graph[rt].append((rc, lt, lc, source))  # undirected for traversal

    def join_path(self, table_a: str, table_b: str) -> list[str] | None:
        """Shortest join path between two tables as a list of ON conditions -- composing
        the single-hop edges into a 3-/4-way path when needed. None if unreachable."""
        if table_a == table_b:
            return []
        queue = deque([(table_a, [])])
        seen = {table_a}
        while queue:
            node, path = queue.popleft()
            for col, target, target_col, _src in self._graph.get(node, []):
                if target in seen:
                    continue
                cond = f"{node}.{col} = {target}.{target_col}"
                if target == table_b:
                    return path + [cond]
                seen.add(target)
                queue.append((target, path + [cond]))
        return None

    def known_join_edges(self, table: str) -> list[str]:
        return sorted({f"{table}.{c} = {rt}.{rc}" for (c, rt, rc, _s) in self._graph.get(table, [])})

    def learn_join(self, left: str, right: str) -> bool:
        """Record a join edge (from a verified query) that the graph did not already have.
        Returns True if it was new. left/right are 'table.column'."""
        if "." not in left or "." not in right or _edge_key(left, right) in self._known_edges:
            return False
        lt, lc = left.split(".", 1)
        rt, rc = right.split(".", 1)
        self._add_edge(lt, lc, rt, rc, "learned")
        return True

    def _add_example(self, source: str, question: str, sql: str, table_columns: dict[str, list[str]]) -> None:
        sql = " ".join((sql or "").split())
        if not sql:
            return
        try:
            analysis = analyze_sql_references(sql, table_columns)
        except Exception:  # noqa: BLE001 -- a malformed history row must not sink the bank
            return
        ex = Example(
            source=source,
            question=" ".join((question or "").split()),
            sql=sql,
            tables=tuple(analysis.tables),
            columns=tuple(analysis.columns),
        )
        self.examples.append(ex)
        # observed join edges, keyed per table, so describe_table can show real links
        for pair in analysis.join_pairs:
            lt, rt = pair.tables
            condition = f"{pair.left_column} = {pair.right_column}"
            self._join_edges.setdefault(lt, set()).add(condition)
            self._join_edges.setdefault(rt, set()).add(condition)

    # ---- navigation -------------------------------------------------------------------
    def tables(self) -> list[tuple[str, str]]:
        tbls = self.metadata.get("tables") or {}
        out = []
        for name in sorted(tbls):
            desc = _short_desc(tbls.get(name))
            out.append((name, desc))
        return out

    def table_description(self, table: str) -> str | None:
        """The table's own description (for get_tables on a single table)."""
        tbls = self.metadata.get("tables") or {}
        if table not in tbls:
            return None
        meta = tbls.get(table)
        if isinstance(meta, dict):
            return " ".join(str(meta.get("long_description") or meta.get("short_description") or "").split())
        return _short_desc(meta)

    def column_names(self, table: str) -> list[str] | None:
        cols = (self.metadata.get("columns") or {}).get(table)
        return list(cols) if cols is not None else None

    def columns_compact(self, table: str, column: str | None = None) -> list[dict[str, str]] | None:
        """One compact line per column -- name, short description, a few value examples. This is
        the token-cheap column list that replaces dumping full descriptions + domains at once."""
        cols = (self.metadata.get("columns") or {}).get(table)
        if cols is None:
            return None
        domains = self.value_domains.get(table, {})
        names = [column] if column else list(cols)
        out = []
        for name in names:
            if name not in cols:
                continue
            out.append({"name": name, "description": _short_desc(cols.get(name)),
                        "examples": _sample_values(domains.get(name))})
        return out

    def column_detail(self, table: str, column: str) -> dict[str, str] | None:
        """The full description + value domain for ONE column (the deep dive)."""
        cols = (self.metadata.get("columns") or {}).get(table)
        if cols is None or column not in cols:
            return None
        meta = cols.get(column)
        long_desc = ""
        if isinstance(meta, dict):
            long_desc = " ".join(str(meta.get("long_description") or meta.get("short_description") or "").split())
        return {"name": column, "table": table, "description": long_desc,
                "values": _format_domain(self.value_domains.get(table, {}).get(column))}

    def describe_table(self, table: str) -> dict[str, Any] | None:
        cols = (self.metadata.get("columns") or {}).get(table)
        if cols is None:
            return None
        table_domains = self.value_domains.get(table, {})
        columns = []
        for name, meta in cols.items():
            columns.append({"name": name, "description": _short_desc(meta),
                            "values": _format_domain(table_domains.get(name))})
        return {
            "table": table,
            "description": _short_desc((self.metadata.get("tables") or {}).get(table)),
            "columns": columns,
            "joins": self.known_join_edges(table),  # canonical (derived + learned) edges
            "observed_joins": sorted(self._join_edges.get(table, set())),
        }

    def exact_match(self, question: str) -> Example | None:
        """Return the gold example whose question matches this one (ignoring case,
        whitespace, trailing punctuation). Gold pairs are offline evals: a question that
        matches one must return its answer, deterministically -- so this is replayed
        verbatim, not re-authored. Only gold (reviewed) examples qualify."""

        target = _normalize_question(question)
        if not target:
            return None
        for ex in self.examples:
            if ex.source == "gold" and ex.question and _normalize_question(ex.question) == target:
                return ex
        return None

    def slot_match(self, question: str) -> tuple[str, "Example"] | None:
        """Find a gold example whose question differs from this one only in literal values,
        and return its SQL with those literals substituted -- deterministic slot adaptation.

        Same proven query shape, new constants (a different year, state, category). Falls
        through (None) unless the substitution changes ONLY literals: it is rejected if the
        adapted SQL parses to different tables/columns than the gold, so a bad swap can never
        silently ship. No LLM.
        """

        user_words = question.split()
        user_lower = [w.lower().strip(".,?!") for w in user_words]
        table_columns = {t: list(c) for t, c in (self.metadata.get("columns") or {}).items()}
        for ex in self.examples:
            if ex.source != "gold" or not ex.question:
                continue
            gold_words = ex.question.split()
            if len(gold_words) != len(user_words):
                continue
            gold_lower = [w.lower().strip(".,?!") for w in gold_words]
            diffs = [i for i in range(len(gold_lower)) if gold_lower[i] != user_lower[i]]
            if not diffs or len(diffs) > _DEFAULTS.slot_match_max_diffs:
                continue
            adapted, ok = ex.sql, True
            for i in diffs:
                old = gold_words[i].strip(".,?!")
                new = user_words[i].strip(".,?!")
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])"
                if not old or not new or not re.search(pattern, adapted):
                    ok = False
                    break
                adapted = re.sub(pattern, new, adapted)
            if not ok:
                continue
            try:
                analysis = analyze_sql_references(adapted, table_columns)
            except Exception:  # noqa: BLE001
                continue
            # safety: only literals may have changed -> structure identical to the gold
            if set(analysis.tables) == set(ex.tables) and set(analysis.columns) == set(ex.columns):
                return adapted, ex
        return None

    def find_examples(self, query: str, *, limit: int = _DEFAULTS.find_examples_limit) -> list[Example]:
        """Rank examples by overlap with the query on STRUCTURE first, words second.

        The query can name business words (matched against the question) and/or schema
        tokens (matched against the tables/columns the example's SQL uses). Exact schema
        overlap dominates, which is what lands the right precedent instead of a fuzzy
        near-miss.
        """

        q = _tokens(query)
        if not q:
            return []
        scored: list[tuple[float, Example]] = []
        for ex in self.examples:
            hay = ex._haystack()
            overlap = q & hay
            if not overlap:
                continue
            # weight schema-token hits (tables/columns) above plain question words
            schema_hits = len(overlap & (set(ex.tables) | {c for col in ex.columns for c in col.replace(".", " ").split()}))
            score = len(overlap) + 2.0 * schema_hits + (0.5 if ex.source == "gold" else 0.0)
            scored.append((score, ex))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [ex for _, ex in scored[:limit]]


def _derive_join_edges(table_columns: dict[str, list[str]]) -> list[tuple]:
    """Derive canonical join edges from the X_ref -> Xs.X_record naming convention.

    Complete and lossless for a regularly-named schema; multi-hop paths are composed from
    these by BFS. Joins that DON'T follow the convention aren't derived here -- the agent
    discovers those and they get learned back via Workspace.learn_join."""

    record_of = {c: t for t, cols in table_columns.items() for c in cols if c.endswith("_record")}
    edges = []
    for table, cols in table_columns.items():
        for col in cols:
            if not col.endswith("_ref"):
                continue
            base = col[:-4]
            best = None
            for rec, rec_table in record_of.items():
                entity = rec[:-7]
                if base == entity or base.endswith("_" + entity):
                    if best is None or len(entity) > len(best[0]):  # longest entity wins
                        best = (entity, rec, rec_table)
            if best:
                edges.append((table, col, best[2], best[1]))
    return edges


def _edge_key(a: str, b: str) -> str:
    return " = ".join(sorted((a, b)))


def _short_desc(meta: Any) -> str:
    if isinstance(meta, dict):
        return " ".join(str(meta.get("short_description") or meta.get("long_description") or "").split())
    return ""


def _normalize_semantic_layer(sl: dict | None) -> dict | None:
    """Accept both authoring shapes for metrics/dimensions/business_terms:

      dict:  {name: {sql, description, ...}, ...}          # original V3-S5
      list:  [{name, sql, description, ...}, ...]          # catalog / UAT authoring

    Downstream (Context._merge_blessed, Workspace.metric / definitions_index) indexes by name,
    so list form is rewritten to a dict. Entries missing ``name`` are skipped.
    """
    if not isinstance(sl, dict):
        return sl
    out = dict(sl)
    for section in ("metrics", "dimensions", "business_terms"):
        body = out.get(section)
        if isinstance(body, list):
            keyed: dict[str, Any] = {}
            for item in body:
                if isinstance(item, dict) and item.get("name"):
                    keyed[str(item["name"])] = {k: v for k, v in item.items() if k != "name"}
            out[section] = keyed
    return out


def _parse_semantic_layer_yaml(text: str) -> dict | None:
    """Parse a semantic_layer YAML/JSON blob. Mirrors _semantic_layer_from_store's parse step
    without needing a schema-shaped store. Used by Workspace.from_catalog_store."""
    if not text:
        return None
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        import json as _json
        return _normalize_semantic_layer(_json.loads(text) or None)
    import yaml
    return _normalize_semantic_layer(yaml.safe_load(text) or None)


def _semantic_layer_from_store(store: Any, schema: str) -> dict | None:
    """The customer's business-definitions/metrics layer from the object store. Authored as YAML
    (semantic_layer.yaml/.yml, preferred -- humans hand-write the metric tree) or JSON
    (semantic_layer.json). None if neither exists."""
    for name in ("semantic_layer.yaml", "semantic_layer.yml"):
        if store.has(schema, name):
            import yaml
            return _normalize_semantic_layer(yaml.safe_load(store.read_text(schema, name)) or None)
    raw = store.get(schema, "semantic_layer.json") or None
    return _normalize_semantic_layer(raw) if isinstance(raw, dict) else raw


def _sample_values(dom: Any, limit: int = _DEFAULTS.example_values_shown) -> str:
    """A few example values for the compact column list -- short, just enough to recognize
    what the column holds. Full domains come from describe_column / profile_column."""
    if not isinstance(dom, dict):
        return ""
    values = dom.get("values") or []
    if not values:
        rng = (dom.get("min"), dom.get("max"))
        return f"range {_v(rng[0])}..{_v(rng[1])}" if rng[0] is not None else ""
    sample = ", ".join(_v(v)[:_DEFAULTS.value_str_width] for v in values[:limit])
    more = "" if dom.get("complete") and len(values) <= limit else " ..."
    return sample + more


def _format_domain(dom: Any) -> str:
    """Render a column's value domain for the agent: the COMPLETE set when small (so it
    can map 'jewellry' -> 'Jewelry' or 'female' -> 'F' and know the value exists), else a
    sample + range."""

    if not isinstance(dom, dict):
        return ""
    values = dom.get("values") or []
    if dom.get("complete"):
        return "values: " + ", ".join(_v(v) for v in values)
    hint = ("e.g. " + ", ".join(_v(v) for v in values[:_DEFAULTS.hint_values_shown])
            + f" ({dom.get('distinct_at_least')}+ distinct)")
    if "min" in dom and "max" in dom:
        hint += f"; range {_v(dom['min'])}..{_v(dom['max'])}"
    return hint


def _v(value: Any) -> str:
    return f"'{value}'" if isinstance(value, str) else str(value)
