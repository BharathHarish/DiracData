"""DiracData v3 -- an analyst agent over a workspace, driven by a durable investigator loop.

The stripped-down architecture: no embeddings, no compiled catalog, no synthesized brief and
no pipeline of personas. A single ANALYST loop explores raw context (schema map + example bank
+ semantic layer) with primitive tools -- looking up definitions, probing the data, building
CTEs -- then an independent VERIFY check sees the result and catches anomalies, and a verified
novel query is RECORDED back as an experience. The INVESTIGATOR wraps this in a durable
decide-loop that decomposes a metric and reconciles the parts. Reuses v2's low-level primitives
(model factory, DuckDB engine, SQL parser).
"""

from diracdata_v3.agent import Analyst, V3Agent, V3Answer
from diracdata_v3.experience import ExperienceStore, JoinStore
from diracdata_v3.investigator import Investigation, Investigator
from diracdata_v3.workspace import Workspace

__all__ = ["Analyst", "V3Agent", "V3Answer", "Workspace", "ExperienceStore", "JoinStore",
           "Investigator", "Investigation"]
