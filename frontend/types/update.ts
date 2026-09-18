export interface UpdateState {
  supported: boolean;
  reason: string | null;
  phase: "idle" | "checking" | "available" | "downloading" | "ready" | "installing" | "error";
  current: { version: string; sha: string | null };
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
  last_result: { status: string; message: string; version: string | null } | null;
}
