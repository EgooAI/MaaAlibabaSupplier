"use client";

import { DeleteColumnOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Empty, Input, InputNumber, Space, Spin, Switch, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import { formatFullDateTime } from "@/domain/time";
import type { ExecutionRunStatus, ExecutionRunSummary } from "@/types/execution";
import { isExecutionTerminal, useExecution } from "./hooks/useExecution";

const STATUS_LABEL: Record<ExecutionRunStatus, { color: string; text: string }> = {
  queued: { color: "default", text: "排队中" },
  running: { color: "processing", text: "执行中" },
  succeeded: { color: "success", text: "已成功" },
  failed: { color: "error", text: "失败" },
  cancelled: { color: "warning", text: "已停止" },
  timeout: { color: "warning", text: "已超时" },
  interrupted: { color: "volcano", text: "重启中断" },
};

const ERROR_TEXT: Record<string, string> = {
  nonzero_exit: "脚本以非零状态退出",
  timeout: "超过执行时限，进程树已终止",
  cancelled: "已按请求停止",
  restart_interrupted: "程序重启，未完成的执行已中断",
  spawn_failed: "无法启动 Python 进程",
  start_failed: "执行任务启动失败",
  termination_unconfirmed: "已请求终止，但进程树未在 5 秒内确认退出",
};

function statusTag(status: ExecutionRunStatus) {
  const item = STATUS_LABEL[status];
  return <Tag color={item.color}>{item.text}</Tag>;
}

function durationText(run: ExecutionRunSummary): string {
  const start = run.started_at ?? run.created_at;
  const end = run.finished_at ?? start;
  const seconds = Math.max(0, Math.round(end - start));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}

function outputText(value: string): string {
  return value || "（暂无输出）";
}

export function ExecutionPanel() {
  const {
    state, detail, script, setScript, timeout, setTimeoutSeconds,
    loading, busy, error, toggle, submit, cancel, openRun, refresh,
  } = useExecution();

  if (loading && !state) {
    return <Card title="远程执行"><Spin /></Card>;
  }
  if (!state) {
    return <Card title="远程执行"><Empty description={error ?? "无法读取远程执行状态"} /></Card>;
  }

  const available = state.interpreter_available;
  const active = Boolean(detail && !isExecutionTerminal(detail.status));
  const columns = [
    {
      title: "提交时间", dataIndex: "created_at", width: 170,
      render: (value: number) => formatFullDateTime(value),
    },
    {
      title: "状态", dataIndex: "status", width: 110,
      render: (value: ExecutionRunStatus) => statusTag(value),
    },
    {
      title: "结果", key: "result", width: 140,
      render: (_: unknown, run: ExecutionRunSummary) => (run.error
        ? <Tooltip title={run.error}><Typography.Text type="secondary">{ERROR_TEXT[run.error] ?? "执行失败"}</Typography.Text></Tooltip>
        : run.exit_code === null ? "—" : `退出码 ${run.exit_code}`),
    },
    {
      title: "耗时", key: "duration", width: 100,
      render: (_: unknown, run: ExecutionRunSummary) => durationText(run),
    },
    {
      title: "输出", key: "output", width: 120,
      render: (_: unknown, run: ExecutionRunSummary) => `${
        run.stdout_chars + run.stderr_chars
      } 字符${run.stdout_truncated || run.stderr_truncated ? "（截断）" : ""}`,
    },
    {
      title: "", key: "open", width: 80,
      render: (_: unknown, run: ExecutionRunSummary) => (
        <Button size="small" type="link" onClick={() => void openRun(run.id)}>查看</Button>
      ),
    },
  ];

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <Card
        title="远程执行"
        extra={<Space>
          <Typography.Text type="secondary">开关版本 {state.generation}</Typography.Text>
          <Switch
            checked={state.enabled}
            loading={busy}
            disabled={!available}
            checkedChildren="已开启"
            unCheckedChildren="已关闭"
            onChange={(checked) => void toggle(checked)}
          />
        </Space>}
      >
        <Space orientation="vertical" size="middle" className="w-full">
          <Alert
            type={state.enabled ? "warning" : "info"}
            showIcon
            title={state.enabled ? "远程执行已开启" : "远程执行已关闭"}
            description={state.enabled
              ? "脚本以当前 Windows 用户权限运行，可读写文件、访问网络并启动其他程序。请仅在受控网络中使用；程序重启后自动关闭，关闭开关或退出时会终止进程树。"
              : "开启后可在本机运行任意 Python 脚本。每次后端启动都恢复关闭状态，需要在此页面重新开启。"}
          />
          {error ? <Alert type="error" showIcon title={error} /> : null}
          {!available ? <Alert type="error" showIcon title="未找到可用的 Python 解释器，无法执行脚本。" /> : null}
          <Descriptions size="small" column={1}>
            <Descriptions.Item label="解释器"><Typography.Text code>{state.interpreter}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="工作目录"><Typography.Text code>{state.cwd}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="限制">
              脚本 {Math.floor(state.limits.max_script_bytes / 1024)} KiB · 输出 {Math.floor(state.limits.max_output_chars / 1024)} K 字符 ·
              超时 {state.limits.min_timeout_s}–{state.limits.max_timeout_s} 秒 ·
              保留最近 {state.limits.max_runs} 次 / {Math.round(state.limits.max_age_s / 3600)} 小时
            </Descriptions.Item>
          </Descriptions>
        </Space>
      </Card>

      <Card title="脚本" extra={<Typography.Text type="secondary">Python（软件自带解释器，不依赖 PowerShell 7）</Typography.Text>}>
        <Space orientation="vertical" size="middle" className="w-full">
          <Input.TextArea
            value={script}
            onChange={(event) => setScript(event.target.value)}
            disabled={!state.enabled}
            autoSize={{ minRows: 8, maxRows: 20 }}
            spellCheck={false}
            style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}
          />
          <Space wrap>
            <Typography.Text type="secondary">超时</Typography.Text>
            <InputNumber
              min={state.limits.min_timeout_s}
              max={state.limits.max_timeout_s}
              value={timeout}
              disabled={!state.enabled || active}
              onChange={(value) => setTimeoutSeconds(value ?? state.limits.default_timeout_s)}
            />
            <Typography.Text type="secondary">秒</Typography.Text>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={busy && !active}
              disabled={!state.enabled || !available || active || !script.trim()}
              onClick={() => void submit()}
            >
              运行
            </Button>
            <Button
              danger
              icon={<StopOutlined />}
              loading={busy && active}
              disabled={!active}
              onClick={() => void cancel()}
            >
              停止
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>
            <Button
              icon={<DeleteColumnOutlined />}
              disabled={!state.enabled || active}
              onClick={() => setScript("")}
            >
              清空脚本
            </Button>
          </Space>
          <Typography.Text type="secondary">
            本次运行未结束前不能提交第二个脚本。停止和超时会终止整个 Python 进程树；超时上限 {state.limits.max_timeout_s} 秒。
          </Typography.Text>
        </Space>
      </Card>

      {detail ? (
        <Card
          title={<Space>执行结果 {statusTag(detail.status)}<Typography.Text type="secondary">{detail.id}</Typography.Text></Space>}
          extra={active ? <Tag color="processing">每 1 秒更新</Tag> : null}
        >
          <Space orientation="vertical" size="middle" className="w-full">
            <Descriptions size="small" column={{ xs: 1, md: 3 }}>
              <Descriptions.Item label="提交">{formatFullDateTime(detail.created_at)}</Descriptions.Item>
              <Descriptions.Item label="耗时">{durationText(detail)}</Descriptions.Item>
              <Descriptions.Item label="退出码">{detail.exit_code ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="超时">{detail.timeout_s} 秒</Descriptions.Item>
              <Descriptions.Item label="标准输出">{detail.stdout_total} 字符</Descriptions.Item>
              <Descriptions.Item label="标准错误">{detail.stderr_total} 字符</Descriptions.Item>
            </Descriptions>
            {detail.error ? <Alert type="warning" showIcon title={ERROR_TEXT[detail.error] ?? detail.error} /> : null}
            {detail.stdout_truncated || detail.stderr_truncated ? (
              <Alert type="warning" showIcon title="输出超过保留上限，后续内容已丢弃。" />
            ) : null}
            <Tabs items={[
              {
                key: "stdout", label: `标准输出（${detail.stdout_total}）`,
                children: <pre className="max-h-[420px] overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100 whitespace-pre-wrap break-all">{outputText(detail.stdout)}</pre>,
              },
              {
                key: "stderr", label: `标准错误（${detail.stderr_total}）`,
                children: <pre className="max-h-[420px] overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100 whitespace-pre-wrap break-all">{outputText(detail.stderr)}</pre>,
              },
              {
                key: "script", label: "脚本",
                children: <pre className="max-h-[420px] overflow-auto rounded bg-slate-100 p-3 text-xs whitespace-pre-wrap break-all">{detail.script || "（脚本内容未保留）"}</pre>,
              },
            ]} />
          </Space>
        </Card>
      ) : null}

      <Card title="最近执行" extra={<Typography.Text type="secondary">仅保留元数据与输出文件，不进入普通日志</Typography.Text>}>
        <Table<ExecutionRunSummary>
          size="small"
          rowKey="id"
          dataSource={state.runs}
          columns={columns}
          pagination={false}
          locale={{ emptyText: "尚无执行记录" }}
          scroll={{ x: 720 }}
        />
      </Card>
    </Space>
  );
}
