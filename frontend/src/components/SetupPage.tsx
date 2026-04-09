import { useState, useEffect, useCallback } from "react";
import { CheckCircle, AlertCircle, Loader, ExternalLink, FolderOpen, Shield, Search } from "lucide-react";
import { getSetupInfo, grantSpAccess, validateSetup } from "../api";
import type { SetupInfoResponse, SetupStatusResponse } from "../types";

type Step = "create" | "grant" | "validate";
type StepStatus = "pending" | "active" | "done" | "error";

interface Props {
  setupStatus: SetupStatusResponse | null;
  onSetupComplete: (experimentPath: string) => void;
}

const STEPS: { key: Step; label: string; icon: typeof FolderOpen }[] = [
  { key: "create", label: "Create Folder", icon: FolderOpen },
  { key: "grant", label: "Grant Access", icon: Shield },
  { key: "validate", label: "Validate", icon: Search },
];

export default function SetupPage({ setupStatus, onSetupComplete }: Props) {
  const [info, setInfo] = useState<SetupInfoResponse | null>(null);
  const [infoLoading, setInfoLoading] = useState(true);
  const [experimentPath, setExperimentPath] = useState("");
  const [currentStep, setCurrentStep] = useState<Step>("create");
  const [stepStatuses, setStepStatuses] = useState<Record<Step, StepStatus>>({
    create: "active",
    grant: "pending",
    validate: "pending",
  });
  const [grantLoading, setGrantLoading] = useState(false);
  const [manualInstructions, setManualInstructions] = useState<string | null>(null);
  const [validateLoading, setValidateLoading] = useState(false);
  const [validateError, setValidateError] = useState<string | null>(null);
  const [isComplete, setIsComplete] = useState(false);

  // If already set up, show the completed state
  useEffect(() => {
    if (setupStatus?.setup_complete && setupStatus.experiment_path) {
      setExperimentPath(setupStatus.experiment_path);
      setIsComplete(true);
      setStepStatuses({ create: "done", grant: "done", validate: "done" });
    }
  }, [setupStatus]);

  // Load setup info (user email, SP name)
  useEffect(() => {
    getSetupInfo()
      .then((data) => {
        setInfo(data);
        if (!experimentPath && data.user_email) {
          setExperimentPath(`/Users/${data.user_email}/agent-sweet`);
        }
      })
      .catch(console.error)
      .finally(() => setInfoLoading(false));
  }, []);

  const handlePathConfirm = useCallback(() => {
    if (!experimentPath.trim()) return;
    setStepStatuses((prev) => ({ ...prev, create: "done", grant: "active" }));
    setCurrentStep("grant");
  }, [experimentPath]);

  const handleGrant = useCallback(async () => {
    setGrantLoading(true);
    setManualInstructions(null);
    try {
      const result = await grantSpAccess(experimentPath);
      if (result.success) {
        setStepStatuses((prev) => ({ ...prev, grant: "done", validate: "active" }));
        setCurrentStep("validate");
      } else {
        setManualInstructions(result.manual_instructions);
      }
    } catch (err) {
      setManualInstructions(
        `Failed to grant access: ${err instanceof Error ? err.message : String(err)}. ` +
        "Please grant permissions manually (see instructions below)."
      );
    } finally {
      setGrantLoading(false);
    }
  }, [experimentPath]);

  const handleSkipGrant = useCallback(() => {
    setStepStatuses((prev) => ({ ...prev, grant: "done", validate: "active" }));
    setCurrentStep("validate");
  }, []);

  const handleValidate = useCallback(async () => {
    setValidateLoading(true);
    setValidateError(null);
    try {
      const result = await validateSetup(experimentPath);
      if (result.success) {
        setStepStatuses((prev) => ({ ...prev, validate: "done" }));
        setIsComplete(true);
        onSetupComplete(experimentPath);
      } else {
        setValidateError(result.error || "Validation failed. Check permissions and try again.");
        setStepStatuses((prev) => ({ ...prev, validate: "error" }));
      }
    } catch (err) {
      setValidateError(err instanceof Error ? err.message : String(err));
      setStepStatuses((prev) => ({ ...prev, validate: "error" }));
    } finally {
      setValidateLoading(false);
    }
  }, [experimentPath, onSetupComplete]);

  const handleReconfigure = useCallback(() => {
    setIsComplete(false);
    setCurrentStep("create");
    setStepStatuses({ create: "active", grant: "pending", validate: "pending" });
    setManualInstructions(null);
    setValidateError(null);
  }, []);

  if (infoLoading) {
    return (
      <div className="setup-page">
        <div className="setup-loading">
          <Loader size={24} className="spinning" />
          <span>Loading setup information...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="setup-page">
      <div className="setup-header">
        <h1>MLflow Experiment Setup</h1>
        <p>
          Configure your MLflow experiment directory so the app can log models and
          track experiments on your behalf. This is a one-time setup.
        </p>
      </div>

      {/* Stepper */}
      <div className="setup-stepper">
        {STEPS.map(({ key, label, icon: Icon }) => {
          const status = stepStatuses[key];
          return (
            <div key={key} className={`setup-step setup-step--${status}`}>
              <span className="setup-step-icon">
                {status === "done" ? (
                  <CheckCircle size={20} />
                ) : status === "error" ? (
                  <AlertCircle size={20} />
                ) : (
                  <Icon size={20} />
                )}
              </span>
              <span className="setup-step-label">{label}</span>
            </div>
          );
        })}
      </div>

      {/* Completed state */}
      {isComplete && (
        <>
          <div className="setup-card setup-success-card">
            <CheckCircle size={32} />
            <h2>Setup Complete</h2>
            <p>Your MLflow experiment directory is configured and accessible.</p>
            <div className="setup-field">
              <label>Experiment Path</label>
              <input type="text" className="deploy-input" readOnly value={experimentPath} />
            </div>
            <p className="setup-hint">
              This path will be pre-filled when you deploy an agent. You can change it
              in the Deploy modal if needed.
            </p>
            <button className="btn btn-ghost" onClick={handleReconfigure}>
              Reconfigure
            </button>
          </div>

          <div className="setup-card">
            <h2>Unity Catalog Access</h2>
            <p>
              To register and deploy models, the app's service principal also needs
              access to your target Unity Catalog and schema. Run these SQL commands
              in a notebook or SQL editor:
            </p>
            <div className="setup-instructions-box">
              <pre>{`GRANT USE CATALOG ON CATALOG <your_catalog> TO \`${info?.sp_id || "sp-client-id"}\`;
GRANT USE SCHEMA ON SCHEMA <your_catalog>.<your_schema> TO \`${info?.sp_id || "sp-client-id"}\`;
GRANT CREATE MODEL ON SCHEMA <your_catalog>.<your_schema> TO \`${info?.sp_id || "sp-client-id"}\`;`}</pre>
            </div>
            <p className="setup-hint">
              Replace <code>&lt;your_catalog&gt;</code> and <code>&lt;your_schema&gt;</code> with
              the catalog and schema you'll use when deploying models.
            </p>
          </div>
        </>
      )}

      {/* Step 1: Create Folder */}
      {!isComplete && currentStep === "create" && (
        <div className="setup-card">
          <h2>Step 1: Create Your Experiments Folder</h2>
          <p>
            Create a folder in your Databricks workspace that will hold your MLflow
            experiments. The app will create experiments inside this folder when you deploy.
          </p>
          <div className="setup-instructions-box">
            <ol>
              <li>Open your Databricks workspace</li>
              <li>Navigate to <strong>Workspace</strong> in the left sidebar</li>
              <li>Go to <strong>Users &gt; {info?.user_email || "your-email"}</strong></li>
              <li>Click the kebab menu (<strong>&#8942;</strong>) &gt; <strong>Create &gt; Folder</strong></li>
              <li>Name it <code>agent-sweet</code> (or any name you prefer)</li>
            </ol>
          </div>
          <div className="setup-field">
            <label>Enter the full path of the experiment you created:</label>
            <input
              type="text"
              className="deploy-input"
              placeholder={`/Users/${info?.user_email || "you@company.com"}/agent-sweet`}
              value={experimentPath}
              onChange={(e) => setExperimentPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handlePathConfirm()}
            />
          </div>
          <div className="setup-actions">
            <button
              className="btn btn-primary"
              disabled={!experimentPath.trim()}
              onClick={handlePathConfirm}
            >
              Continue
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Grant Access */}
      {!isComplete && currentStep === "grant" && (
        <div className="setup-card">
          <h2>Step 2: Grant App Access</h2>
          <p>
            The app needs <strong>Can Manage</strong> permission on your directory so
            it can create experiments and log models. We'll try to do this automatically.
          </p>
          {info && (
            <div className="setup-sp-info">
              <span className="setup-sp-label">Service Principal:</span>
              <code>{info.sp_display_name}</code>
              <span className="setup-sp-id">(ID: {info.sp_id})</span>
            </div>
          )}

          {manualInstructions && (
            <div className="setup-instructions-box setup-instructions-warning">
              <pre>{manualInstructions}</pre>
            </div>
          )}

          <div className="setup-actions">
            {!manualInstructions ? (
              <button
                className="btn btn-primary"
                disabled={grantLoading}
                onClick={handleGrant}
              >
                {grantLoading ? (
                  <>
                    <Loader size={14} className="spinning" />
                    Granting Access...
                  </>
                ) : (
                  "Grant Access Automatically"
                )}
              </button>
            ) : (
              <button className="btn btn-primary" onClick={handleSkipGrant}>
                I've granted access manually — Continue
              </button>
            )}
          </div>
        </div>
      )}

      {/* Step 3: Validate */}
      {!isComplete && currentStep === "validate" && (
        <div className="setup-card">
          <h2>Step 3: Validate Setup</h2>
          <p>
            We'll verify that the app's service principal can access your experiment
            directory at <code>{experimentPath}</code>.
          </p>

          {validateError && (
            <div className="setup-instructions-box setup-instructions-warning">
              <p>{validateError}</p>
            </div>
          )}

          <div className="setup-actions">
            <button
              className="btn btn-ghost"
              onClick={() => {
                setCurrentStep("grant");
                setStepStatuses((prev) => ({ ...prev, grant: "active", validate: "pending" }));
                setValidateError(null);
              }}
            >
              Back
            </button>
            <button
              className="btn btn-primary"
              disabled={validateLoading}
              onClick={handleValidate}
            >
              {validateLoading ? (
                <>
                  <Loader size={14} className="spinning" />
                  Validating...
                </>
              ) : validateError ? (
                "Retry Validation"
              ) : (
                "Validate"
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
