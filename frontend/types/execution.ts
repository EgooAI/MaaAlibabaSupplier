export type ExecutionRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timeout" | "interrupted";

export interface ExecutionLimits {
  max_script_bytes: number;
  max_output_chars: number;
  min_timeout_s: number;
  default_timeout_s: number;
  max_timeout_s: number;
  max_runs: number;
  max_age_s: number;
}

export interface ExecutionRunSummary {
  id: string;
  status: ExecutionRunStatus;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  timeout_s: number;
  exit_code: number | null;
  cancel_requested: boolean;
  script_bytes: number;
  error: string | null;
  stdout_chars: number;
  stderr_chars: number;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
}

export interface ExecutionRunDetail extends ExecutionRunSummary {
  script: string;
  stdout: string;
  stderr: string;
  stdout_offset: number;
  stderr_offset: number;
  stdout_total: number;
  stderr_total: number;
}

export interface ExecutionState {
  enabled: boolean;
  generation: number;
  start_token: string;
  interpreter: string;
  interpreter_available: boolean;
  cwd: string;
  active_run_id: string | null;
  limits: ExecutionLimits;
  runs: ExecutionRunSummary[];
}

export interface ExecutionRunInput {
  id: string;
  start_token: string;
  generation: number;
  script: string;
  timeout_s: number;
}
