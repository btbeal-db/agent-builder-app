import { useState, useEffect, useCallback } from "react";
import { CheckCircle, AlertCircle, Loader, FolderOpen, Shield, Search, Zap } from "lucide-react";
import { autoSetup, getSetupInfo, validateSetup } from "../api";
import type { SetupInfoResponse, SetupStatusResponse } from "../types";

type Step = "create" | "grant" | "validate";
type StepStatus = "pending" | "active" | "done" | "error";
type Mode = "auto" | "manual";

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
  const [mode, setMode] = useState<Mode>("auto");
  const [currentStep, setCurrentStep] = useState<Step>("create");
  const [stepStatuses, setStepStatuses] = useState<Record<Step, StepStatus>>({
    create: "active",
    grant: "pending",
    validate: "pending",
  });
  const [validateLoading, setValidateLoading] = useState(false);
  const [validateError, setValidateError] = useState<string | null>(null);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoError, setAutoError] = useState<string | null>(null);
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

  const handleAutoSetup = useCallback(async () => {
    setAutoLoading(true);
    setAutoError(null);
    try {
      const result = await autoSetup();
      if (result.success) {
        setStepStatuses({ create: "done", grant: "done", validate: "done" });
        setIsComplete(true);
        onSetupComplete(experimentPath);
      } else {
        setAutoError(result.error || "Auto-setup failed. Try the manual flow.");
      }
    } catch (err) {
      setAutoError(err instanceof Error ? err.message : String(err));
    } finally {
      setAutoLoading(false);
    }
  }, [experimentPath, onSetupComplete]);

  const handlePathConfirm = useCallback(() => {
    setStepStatuses((prev) => ({ ...prev, create: "done", grant: "active" }));
    setCurrentStep("grant");
  }, []);

  const handleGrantDone = useCallback(() => {
    setStepStatuses((prev) => ({ ...prev, grant: "done", validate: "active" }));
    setCurrentStep("validate");
  }, []);

  const handleValidate = useCallback(async () => {
    setValidateLoading(true);
    setValidateError(null);
    try {
      const result = await validateSetup();
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
    setMode("auto");
    setCurrentStep("create");
    setStepStatuses({ create: "active", grant: "pending", validate: "pending" });
    setValidateError(null);
    setAutoError(null);
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

  const showManualStepper = !isComplete && mode === "manual";

  return (
    <div className="setup-page">
      <div className="setup-header">
        <h1>MLflow Experiment Setup</h1>
        <p>
          Configure your MLflow experiment directory so the app can log models and
          track experiments on your behalf. This is a one-time setup.
        </p>
      </div>

      {/* Stepper — only shown for the manual flow */}
      {showManualStepper && (
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
      )}

      {/* Completed state */}
      {isComplete && (
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
      )}

      {/* Auto-setup (default) */}
      {!isComplete && mode === "auto" && (
        <div className="setup-card">
          <h2>
            <Zap size={20} style={{ verticalAlign: "-3px", marginRight: "0.4rem" }} />
            Set up automatically
          </h2>
          <p>
            We'll create the folder in your workspace and grant the app's service
            principal <strong>Can Manage</strong> access — both run with your
            on-behalf-of token, so no admin help is needed.
          </p>

          {info && (
            <div className="setup-sp-info">
              <span className="setup-sp-label">Service Principal:</span>
              <code>{info.sp_display_name}</code>
              <span className="setup-sp-id">(ID: {info.sp_id})</span>
            </div>
          )}

          <div className="setup-field">
            <label>Experiment path:</label>
            <input
              type="text"
              className="deploy-input"
              value={experimentPath}
              readOnly
            />
          </div>

          {autoError && (
            <div className="setup-instructions-box setup-instructions-warning">
              <p>{autoError}</p>
              <p style={{ marginTop: "0.5rem" }}>
                If this keeps failing, try the{" "}
                <button
                  className="btn btn-link"
                  onClick={() => {
                    setAutoError(null);
                    setMode("manual");
                  }}
                >
                  manual flow
                </button>
                .
              </p>
            </div>
          )}

          <div className="setup-actions">
            <button
              className="btn btn-primary"
              disabled={autoLoading}
              onClick={handleAutoSetup}
            >
              {autoLoading ? (
                <>
                  <Loader size={14} className="spinning" />
                  Setting up...
                </>
              ) : (
                "Set Up Automatically"
              )}
            </button>
            <button
              className="btn btn-link"
              onClick={() => setMode("manual")}
            >
              Set up manually instead →
            </button>
          </div>
        </div>
      )}

      {/* Manual: Step 1: Create Folder */}
      {!isComplete && mode === "manual" && currentStep === "create" && (
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
              <li>Name it <code>agent-sweet</code></li>
            </ol>
          </div>
          <div className="setup-field">
            <label>Experiment path:</label>
            <input
              type="text"
              className="deploy-input"
              readOnly
              value={experimentPath}
            />
          </div>
          <div className="setup-actions">
            <button className="btn btn-link" onClick={() => setMode("auto")}>
              ← Try auto-setup instead
            </button>
            <button
              className="btn btn-primary"
              onClick={handlePathConfirm}
            >
              Continue
            </button>
          </div>
        </div>
      )}

      {/* Manual: Step 2: Grant Access */}
      {!isComplete && mode === "manual" && currentStep === "grant" && (
        <div className="setup-card">
          <h2>Step 2: Grant App Access</h2>
          <p>
            The app's service principal needs <strong>Can Manage</strong> permission on
            your folder so it can create experiments and log models.
          </p>
          {info && (
            <div className="setup-sp-info">
              <span className="setup-sp-label">Service Principal:</span>
              <code>{info.sp_display_name}</code>
              <span className="setup-sp-id">(ID: {info.sp_id})</span>
            </div>
          )}
          <div className="setup-instructions-box">
            <ol>
              <li>Open your Databricks workspace</li>
              <li>Navigate to <strong>Workspace</strong> &gt; find <code>{experimentPath}</code></li>
              <li>Right-click the folder &gt; <strong>Permissions</strong></li>
              <li>Search for <strong>{info?.sp_display_name || "the service principal"}</strong> (ID: <code>{info?.sp_id || "sp-client-id"}</code>)</li>
              <li>Set permission to <strong>Can Manage</strong></li>
              <li>Click <strong>Save</strong></li>
            </ol>
          </div>
          <div className="setup-actions">
            <button
              className="btn btn-ghost"
              onClick={() => {
                setCurrentStep("create");
                setStepStatuses((prev) => ({ ...prev, create: "active", grant: "pending" }));
              }}
            >
              Back
            </button>
            <button className="btn btn-primary" onClick={handleGrantDone}>
              I've granted access — Continue
            </button>
          </div>
        </div>
      )}

      {/* Manual: Step 3: Validate */}
      {!isComplete && mode === "manual" && currentStep === "validate" && (
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
