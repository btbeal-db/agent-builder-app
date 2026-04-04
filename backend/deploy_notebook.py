# Databricks notebook source

# COMMAND ----------

# COMMAND ----------

import json, subprocess
params = json.loads(dbutils.widgets.get("params_json"))  # noqa: F821
git_ref = params.get("git_ref", "main")
pkg = f"git+https://github.com/btbeal-db/agent-sweet.git@{git_ref}"
print(f"Installing {pkg}")
subprocess.check_call(["pip", "install", pkg, "--upgrade", "--quiet"])
dbutils.library.restartPython()  # noqa: F821

# COMMAND ----------

import json
import tempfile
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists
from databricks.sdk.service.serving import (
    AiGatewayConfig,
    AiGatewayInferenceTableConfig,
    EndpointCoreConfigInput,
    ServedEntityInput,
)

from backend.deploy_helpers import extract_resources, collect_code_paths
from backend.schema import GraphDef

# COMMAND ----------

# Parse deployment config
graph_def = GraphDef.model_validate(json.loads(params["graph_json"]))
model_name = params["model_name"]
catalog = params["catalog"]
schema_name = params["schema_name"]
experiment_base = params["experiment_base"]
lakebase_conn_string = params.get("lakebase_conn_string", "")
deployed_by = params.get("deployed_by", "unknown")

fq_model_name = f"{catalog}.{schema_name}.{model_name}"
endpoint_name = model_name.replace("_", "-")
experiment_path = f"{experiment_base}/{model_name}"

print(f"Model: {fq_model_name}")
print(f"Experiment: {experiment_path}")
print(f"Deployed by: {deployed_by}")
print(f"Endpoint: {endpoint_name}")

# COMMAND ----------

# Step 1: Log model to MLflow

mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
experiment = mlflow.set_experiment(experiment_path)

with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    f.write(graph_def.model_dump_json())
    graph_def_path = f.name

resources = extract_resources(graph_def)

import backend
backend_dir = Path(backend.__file__).parent
python_model_path = str(backend_dir / "mlflow_model.py")

code_paths = collect_code_paths()

requirements_path = backend_dir.parent / "requirements-serving.txt"

with mlflow.start_run() as run:
    # Tag the run with provenance info
    mlflow.set_tag("deployed_by", deployed_by)
    mlflow.set_tag("agent_name", model_name)
    mlflow.set_tag("endpoint_name", endpoint_name)

    log_kwargs = dict(
        artifact_path="agent",
        python_model=python_model_path,
        artifacts={"graph_def": graph_def_path},
        code_paths=code_paths,
        resources=resources if resources else None,
    )
    if requirements_path.exists():
        log_kwargs["pip_requirements"] = str(requirements_path)

    model_info = mlflow.pyfunc.log_model(**log_kwargs)
    run_id = run.info.run_id

print(f"Model logged. Run ID: {run_id}")

# COMMAND ----------

# Step 2: Register in Unity Catalog

mv = mlflow.register_model(
    model_uri=model_info.model_uri,
    name=fq_model_name,
)
print(f"Registered {fq_model_name} version {mv.version}")

# COMMAND ----------

# Step 3: Create/update serving endpoint

w = WorkspaceClient()

env_vars = {
    "ENABLE_MLFLOW_TRACING": "true",
    "MLFLOW_EXPERIMENT_ID": experiment.experiment_id,
}
if lakebase_conn_string:
    env_vars["LAKEBASE_CONN_STRING"] = lakebase_conn_string

served_entity = ServedEntityInput(
    entity_name=fq_model_name,
    entity_version=str(mv.version),
    environment_vars=env_vars,
    scale_to_zero_enabled=True,
    workload_size="Small",
)

ai_gateway = AiGatewayConfig(
    inference_table_config=AiGatewayInferenceTableConfig(
        catalog_name=catalog,
        schema_name=schema_name,
        table_name_prefix=endpoint_name,
        enabled=True,
    ),
)

try:
    w.serving_endpoints.create(
        name=endpoint_name,
        config=EndpointCoreConfigInput(
            name=endpoint_name,
            served_entities=[served_entity],
        ),
        ai_gateway=ai_gateway,
    )
    print(f"Creating endpoint '{endpoint_name}'...")
except ResourceAlreadyExists:
    w.serving_endpoints.update_config(
        name=endpoint_name,
        served_entities=[served_entity],
    )
    w.serving_endpoints.put_ai_gateway(
        name=endpoint_name,
        inference_table_config=AiGatewayInferenceTableConfig(
            catalog_name=catalog,
            schema_name=schema_name,
            table_name_prefix=endpoint_name,
            enabled=True,
        ),
    )
    print(f"Updated existing endpoint '{endpoint_name}'")

host = w.config.host.rstrip("/")
endpoint_url = f"{host}/serving-endpoints/{endpoint_name}/invocations"

# COMMAND ----------

# Return result
dbutils.notebook.exit(json.dumps({  # noqa: F821
    "success": True,
    "run_id": run_id,
    "model_version": str(mv.version),
    "endpoint_url": endpoint_url,
}))
