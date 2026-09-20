"use client";

import { useState } from "react";
import { CloudDownloadOutlined, InfoCircleOutlined, ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Progress, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { useAuth } from "@/features/auth/AuthProvider";
import type { UpdateState } from "@/types/update";
import { useAppUpdate } from "./hooks/useAppUpdate";

const phaseLabels: Record<UpdateState["phase"], string> = {
  idle: "待检查", checking: "正在检查", available: "有可用版本", downloading: "正在下载",
  ready: "可安装", installing: "正在更新", error: "更新出错",
};

function githubLink(repository: string, path: string, children: string) {
  return repository
    ? <Typography.Link href={`https://github.com/${repository}${path}`} target="_blank" rel="noopener noreferrer">{children}</Typography.Link>
    : children;
}

function commitLink(repository: string, sha: string | null) {
  return sha ? githubLink(repository, `/commit/${sha}`, sha) : "未知";
}

function SourceDetails({ source }: { source: UpdateState["source"] }) {
  return (
    <div className="space-y-1">
      <div>仓库：{source.repository || "未配置"}</div>
      <div>分支名称：{source.branch || "未配置"}</div>
      <div>工作流：{source.workflow || "未配置"}</div>
      <div>构建产物：{source.artifact || "未配置"}</div>
    </div>
  );
}

export function UpdateCard() {
  const { phase, generation } = useAuth();
  if (phase !== "authenticated") return <Card title="程序更新"><Typography.Text type="secondary">登录验证完成后可管理应用更新。</Typography.Text></Card>;
  return <UpdateControls key={generation} />;
}

function UpdateControls() {
  const { state, pending, error, installResult, activePhase, restarting, run } = useAppUpdate();
  const [confirmation, setConfirmation] = useState<UpdateState["candidate"]>(null);
  const candidate = state?.candidate;
  const disabled = !state?.supported || pending !== null || activePhase || restarting;
  const canDownload = candidate && (state.phase === "available" || state.phase === "error");
  const progress = state?.total_bytes && state.total_bytes > 0
    ? Math.min(100, Math.max(0, Math.round(state.downloaded_bytes / state.total_bytes * 100))) : null;
  const repository = state?.source.repository ?? "";
  const branch = state?.source.branch ?? "";

  return <Card title="程序更新" extra={<Tag color={restarting ? "processing" : state?.phase === "ready" ? "success" : "default"}>{restarting ? "等待重新连接" : state ? phaseLabels[state.phase] : error ? "状态不可用" : "读取状态"}</Tag>}>
    <Space orientation="vertical" size="middle" className="w-full">
      <Typography.Text type="secondary">手动检查指定 GitHub Actions 来源并下载构建产物。页面不会自动检查新版本；安装后将立即重启程序。</Typography.Text>
      {!state && pending ? <Spin size="small" aria-label="读取更新状态" /> : null}
      {state ? <>
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} styles={{ content: { overflowWrap: "anywhere", minWidth: 0 } }} items={[
          { key: "version", label: "当前版本", children: state.current.run_id ? githubLink(repository, `/actions/runs/${state.current.run_id}`, state.current.version) : state.current.version },
          { key: "sha", label: "当前提交", children: commitLink(repository, state.current.sha) },
          { key: "branch", label: "目标分支", children: <Space size={4} wrap>
            {branch ? githubLink(repository, `/tree/${branch}`, branch) : "未配置"}
            <span>（{repository || "未配置"}）</span>
            <Tooltip title={<SourceDetails source={state.source} />}>
              <InfoCircleOutlined aria-label="更新来源详情" className="cursor-help text-slate-400" />
            </Tooltip>
          </Space> },
        ]} />
        {!state.supported ? <Alert type="info" showIcon title="此运行环境不支持应用更新" description={state.reason || "请使用支持更新的打包版本；源码开发环境无法安装更新。"} /> : null}
        {candidate ? <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
          <Descriptions title="候选版本" size="small" column={{ xs: 1, sm: 2 }} styles={{ content: { overflowWrap: "anywhere", minWidth: 0 } }} items={[
            { key: "version", label: "版本", children: candidate.version },
            { key: "sha", label: "提交", children: commitLink(repository, candidate.sha) },
            { key: "date", label: "构建时间", children: candidate.created_at },
            { key: "run", label: "构建记录", children: /^https:\/\//i.test(candidate.url) ? <Typography.Link href={candidate.url} target="_blank" rel="noopener noreferrer">#{candidate.run_id} · 第 {candidate.run_attempt} 次</Typography.Link> : `#${candidate.run_id} · 第 ${candidate.run_attempt} 次` },
          ]} />
        </div> : null}
        {state.phase === "downloading" || state.downloaded_bytes > 0 ? <div aria-live="polite">
          {progress !== null ? <Progress percent={progress} status={state.phase === "error" ? "exception" : state.phase === "downloading" ? "active" : "normal"} /> : null}
          <Typography.Text type="secondary">已下载 {(state.downloaded_bytes / 1048576).toFixed(1)} MiB{state.total_bytes === null ? "，总大小未知" : ` / ${(state.total_bytes / 1048576).toFixed(1)} MiB`}</Typography.Text>
        </div> : null}
        {state.last_result ? <Alert type="info" showIcon title="上次更新结果" description={`${state.last_result.status}：${state.last_result.message}${state.last_result.version ? `（${state.last_result.version}）` : ""}`} /> : null}
      </> : null}
      {error || state?.error ? <Alert type="error" showIcon title="更新请求失败" description={error || state?.error} /> : null}
      {restarting ? <Alert type={installResult === "uncertain" ? "warning" : "info"} showIcon
        title={installResult === "uncertain" ? "无法确认安装请求是否已接受" : "正在更新，等待程序重启"}
        description={installResult === "uncertain" ? "连接中断或响应无效，安装可能已经开始。请手动刷新查看状态，不要重复提交安装。" : "服务可能暂时断开。请求已进入安装流程，尚未确认安装完成；请在程序重启后手动重新连接。"}
      /> : null}
      {restarting ? <Button icon={<ReloadOutlined />} onClick={() => window.location.reload()}>手动刷新并重新连接</Button> : <Space wrap>
        <Button icon={<SyncOutlined />} disabled={disabled} loading={pending === "check" || state?.phase === "checking"} onClick={() => void run("check")}>检查更新</Button>
        {canDownload ? <Button type="primary" icon={<CloudDownloadOutlined />} disabled={disabled} loading={pending === "download"} onClick={() => void run("download", candidate.id)}>{state.phase === "error" || error ? "重试下载" : "下载更新"}</Button> : null}
        {state?.phase === "ready" && candidate ? <Button danger type="primary" disabled={disabled || Boolean(error)} loading={pending === "install"} onClick={() => setConfirmation(candidate)}>安装并立即重启</Button> : null}
        {error || state?.phase === "error" ? <Button disabled={pending !== null} onClick={() => void run("status")}>刷新本地状态</Button> : null}
      </Space>}
      <ActionConfirmModal open={confirmation !== null} title="安装更新并立即重启" okText="确认安装并重启"
        warning="安装将立即中断正在执行的任务，包括消息发送和批量发送，不会等待任务结束。已发送消息的结果可能未知；重启后请先核实发送记录和客户会话，不要直接重试，以免重复发送。"
        details={confirmation ? [{ label: "版本", value: confirmation.version }, { label: "提交", value: <span className="break-all">{confirmation.sha}</span> }] : []}
        onCancel={() => setConfirmation(null)} onConfirm={() => {
          if (!confirmation) return;
          const id = confirmation.id;
          setConfirmation(null);
          void run("install", id);
        }} />
    </Space>
  </Card>;
}
