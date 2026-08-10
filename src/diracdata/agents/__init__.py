"""diracdata.agents -- the user-facing agent builders (the create_react_agent equivalents).

    from diracdata.agents import data_analyst
    from diracdata.model_providers import FireworksAI
    analyst = data_analyst(schema="ecommerce", model=FireworksAI("deepseek-v4-flash"),
                           conversation="sess-1", memory=True)
    analyst.ask("Which market has the weakest gross profit, and why?")

The internal loop + phases that these build on live in diracdata.harness.
"""

from diracdata.agents.analyst import DataAnalyst, data_analyst

__all__ = ["data_analyst", "DataAnalyst"]
