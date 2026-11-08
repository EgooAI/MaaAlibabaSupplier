export interface UpdateState {
  supported: boolean;
  auto: boolean;
  reason: string | null;
  phase: "idle" | "checking" | "downloading" | "ready" | "installing" | "error";
  current: { version: string; sha: string | null; run_id?: number | null };
  source: { repository: string; branch: string; workflow: string; artifact: string };
  candidate: {
    id: string;
    version: string;
    sha: string;
    run_id: number;
    run_attempt: number;
    created_at: string;
    url: string;
  } | null;
  downloaded_bytes: number;
  total_bytes: number | null;
  error: string | null;
  last_result: { status: string; message: string; version: string | null; executed_at?: number; completed_at?: number } | null;
}

export interface InstallationNotice {
  id: string;
  version: string;
  executed_at: number;
  completed_at: number;
}
