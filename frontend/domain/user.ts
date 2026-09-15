import type { SelfInfo } from "@/types/home";

/** 姓名展示单源，避免 AppShell/ProfileDrawer 各写一次 join 逻辑。空姓名回退 login_id。 */
export function getDisplayName(selfInfo: Pick<SelfInfo, "first_name" | "last_name" | "login_id">): string {
  return [selfInfo.first_name, selfInfo.last_name].filter(Boolean).join(" ") || selfInfo.login_id;
}
