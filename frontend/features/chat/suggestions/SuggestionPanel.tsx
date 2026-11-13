"use client";

import { Button, Select, Spin, Switch } from "antd";
import { TimeText } from "@/components/TimeText";
import { PromptEditorField } from "@/features/agent/components/PromptEditorField";
import { ToolCallCard } from "@/features/agent/timeline/ToolCallCard";
import type { SuggestionRun } from "@/types/suggestions";
import type { useSuggestions } from "./useSuggestions";

type Workspace = ReturnType<typeof useSuggestions>;

export function SuggestionAgentSelect({ workspace, disabled }: { workspace: Workspace; disabled: boolean }) {
  return <Select size="small" aria-label="建议 Agent" variant="borderless" className="max-w-36 min-w-24"
    placeholder="选择 Agent" value={workspace.agentId || undefined} loading={workspace.loading} disabled={disabled}
    onOpenChange={(visible) => { if (visible) void workspace.loadAgents(); }}
    onChange={workspace.selectAgent}
    options={workspace.catalog?.agents.map((agent) => ({ value: agent.apid, label: agent.name, disabled: !agent.enabled }))} />;
}

export function SuggestionPanel({ workspace: w, canUseAi, blocked }: { workspace: Workspace; canUseAi: boolean; blocked: boolean }) {
  const { selected, draft, current } = w;
  const runnable = canUseAi && Boolean(selected?.enabled && draft?.tools.includes("reply_customer") && draft.prompt.trim());
  const waiting = Boolean(w.pending || w.submitting);
  return <section aria-label="AI 建议与调试" className="flex min-h-0 flex-col gap-3 p-3 text-sm sm:p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <strong>AI 建议</strong>
      <SuggestionAgentSelect workspace={w} disabled={blocked || w.saving} />
      <Button type="text" size="small" onClick={() => w.setOpen(false)}>收起</Button>
    </div>
    {w.loading ? <div role="status"><Spin size="small" /> 正在读取 Agent 与运行记录</div> : null}
    {w.error ? <div role="alert" className="text-amber-700">{w.error}
      {w.pending ? <Button size="small" disabled={blocked || w.submitting} onClick={() => void w.recover()}>恢复原请求</Button>
        : !w.catalog ? <Button size="small" onClick={() => void w.show()}>重新加载</Button> : null}
    </div> : null}
    {w.notice ? <p role="status" className="text-xs text-slate-500">{w.notice}</p> : null}
    {current ? <div className="text-xs text-slate-500">
      本轮：{current.agentName} · {current.pinned.kind === "draft" ? "未保存提示词" : "已保存提示词"}
      <div>基于 {current.context.messageCount} 条消息 · 截至 <TimeText value={current.context.lastMessageAt} /></div>
      {w.newMessages ? <div className="mt-1 text-amber-700">会话有新消息
        <Button size="small" type="link" disabled={!runnable || waiting} onClick={() => void w.generate(true)}>更新上下文并测试</Button>
      </div> : null}
    </div> : null}
    <div className="flex flex-wrap gap-2">
      <Button type="primary" loading={waiting} disabled={!runnable || waiting} onClick={() => void w.generate()}>
        {w.editing ? "试用修改" : current ? "重新生成" : "生成建议"}
      </Button>
      <Button disabled={!selected} onClick={() => w.setEditing(!w.editing)}>{w.editing ? "收起提示词" : "编辑提示词"}</Button>
      {w.editing ? <Button disabled={blocked || !w.dirty || w.saving} loading={w.saving} onClick={() => void w.save()}>保存提示词</Button> : null}
    </div>
    {!w.loading && w.catalog && !w.catalog.agents.length ? <p className="text-xs text-slate-500">暂无已启用且具备回复能力的 Agent，请创建回复 Agent。</p> : null}
    {w.editing && draft && selected ? <div className="space-y-3 rounded-lg border border-slate-200 p-3">
      <div className="text-xs text-slate-500">{w.dirty ? "未保存修改 · 可直接试用" : "与已保存配置一致"}</div>
      <PromptEditorField value={draft.prompt} onChange={(prompt) => w.changeDraft({ ...draft, prompt })} initialMode="edit" minRows={6} maxRows={14} />
      <details>
        <summary className="cursor-pointer text-xs text-slate-500">模型、工具与历史版本</summary>
        <div className="mt-3 flex flex-col gap-3">
          <label>模型等级 <Select aria-label="建议模型等级" value={draft.level} onChange={(level) => w.changeDraft({ ...draft, level })}
            options={Array.from({ length: 5 }, (_, value) => ({ value, label: `Level ${value}` }))} /></label>
          <Select mode="multiple" aria-label="建议工具" value={draft.tools} onChange={(tools) => w.changeDraft({ ...draft, tools })}
            options={w.tools.map((tool) => ({ value: tool.name, label: tool.title ?? tool.name }))} />
          <label className="flex items-center gap-2"><Switch size="small" checked={draft.requires_tool}
            onChange={(requires_tool) => w.changeDraft({ ...draft, requires_tool })} />必须调用工具</label>
          <Button size="small" onClick={() => void w.loadRevisions()}>读取历史版本</Button>
          {w.revisions.length ? <Select aria-label="载入提示词历史版本" placeholder="载入一个历史版本到草稿" value={null}
            options={w.revisions.map((revision) => ({ value: revision.id, label: `${revision.created_at} ${revision.note}` }))}
            onChange={(id) => {
              const revision = w.revisions.find((item) => item.id === id)!;
              w.changeDraft({ ...draft, prompt: revision.prompt, level: revision.intelevel, tools: revision.tools, requires_tool: Boolean(revision.requires_tool) });
            }} /> : null}
          <Button size="small" onClick={() => void w.loadSaved()}>载入已保存配置</Button>
        </div>
      </details>
    </div> : null}
    {waiting ? <div role="status" className="flex items-center gap-2 text-slate-500"><Spin size="small" />正在生成建议，可继续修改提示词</div> : null}
    {current?.status === "failed" ? <p role="alert" className="text-amber-700">{current.error}</p> : null}
    {current?.result ? <RunResult run={current} workspace={w} disabled={blocked} /> : null}
    {w.runs[1]?.result ? <details><summary className="cursor-pointer text-slate-500">对比上一轮 · {w.runs[1].agentName}</summary>
      <RunResult run={w.runs[1]} workspace={w} disabled={blocked} />
    </details> : null}
    <Button size="small" type="link" className="self-start" disabled={blocked || !w.catalog || w.saving} onClick={() => void w.save(true)}>创建回复 Agent</Button>
  </section>;
}

function RunResult({ run, workspace: w, disabled }: { run: SuggestionRun; workspace: Workspace; disabled: boolean }) {
  const result = run.result!;
  return <div className="space-y-3">
    {result.restricted ? <p className="text-xs text-amber-700">工具调用达到上限，已保留生成的候选。</p> : null}
    {result.items.map((item, index) => <article key={index} className="space-y-2 rounded-xl border border-blue-100 bg-blue-50/50 p-3">
      <div className="whitespace-pre-wrap break-words">{item.content}</div>
      {item.zh && item.zh !== item.content ? <p className="whitespace-pre-wrap text-xs text-slate-500">{item.zh}</p> : null}
      <Button size="small" disabled={disabled || w.adopting} onClick={() => void w.adopt(run, index)}>插入回复</Button>
    </article>)}
    <details>
      <summary className="cursor-pointer text-xs text-slate-500">判断理由、运行提示词与工具调用</summary>
      <p className="my-2 whitespace-pre-wrap text-xs">{result.reasoning}</p>
      <details><summary className="cursor-pointer text-xs">本轮提示词快照</summary><pre className="my-2 whitespace-pre-wrap break-words text-xs">{run.pinned.prompt}</pre></details>
      {result.toolCalls.map((call, index) => <ToolCallCard key={call.callId ?? index} call={call} simulated />)}
    </details>
  </div>;
}
