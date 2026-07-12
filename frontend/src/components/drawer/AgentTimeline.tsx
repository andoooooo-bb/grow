// リレー・タイムライン（#19, detail モード）: ai_jobs の履歴（createdAt 昇順）を
// 「計画AI → 実行AI → あなた」のコンパクトなステップ表示にする。
// ステップ状態: 完了(succeeded/failed)=塗り / 実行中(running)=点滅ドット / 未来(queued)=グレー。
// 終端は常に「あなた」— 全ジョブが終わればあなたの番（点滅）、タスク完了なら塗り。
// ジョブ0件（未読込・取得失敗を含む）は非表示。ステータス変化（SSE）のたび再取得する。

import { useEffect } from 'react';
import { useBoardStore } from '../../store/board.ts';
import type { AiJob, Task } from '../../types/domain.ts';
import { AGENT_ROLE_META, JOB_KIND_ROLE } from '../../types/domain.ts';
import './AgentTimeline.css';

interface AgentTimelineProps {
  task: Task;
}

type StepState = 'done' | 'active' | 'future' | 'failed';

/** ジョブ種別 → タイムラインの役割ラベル（未知 kind は素通しで前方互換） */
export function jobRoleLabel(kind: AiJob['kind']): string {
  const role = JOB_KIND_ROLE[kind] as keyof typeof AGENT_ROLE_META | undefined;
  return role !== undefined ? AGENT_ROLE_META[role].label : kind;
}

function jobStepState(status: AiJob['status']): StepState {
  if (status === 'succeeded') return 'done';
  if (status === 'running') return 'active';
  if (status === 'failed') return 'failed';
  return 'future'; // queued
}

export function AgentTimeline({ task }: AgentTimelineProps) {
  const jobs = useBoardStore((s) => s.jobs[task.id]);
  const loadJobs = useBoardStore((s) => s.loadJobs);

  // ドロワーを開いたとき＋SSE でステータスが動いたときに履歴を追いかける
  useEffect(() => {
    void loadJobs(task.id);
  }, [task.id, task.status, loadJobs]);

  if (jobs === undefined || jobs.length === 0) return null;

  // 終端「あなた」: ジョブが動いている間は未来、全て終われば active、タスク完了で塗り
  const busy = jobs.some((j) => j.status === 'queued' || j.status === 'running');
  const youState: StepState = busy ? 'future' : task.status === 'done' ? 'done' : 'active';

  const steps = [
    ...jobs.map((job) => ({
      key: job.id,
      label: jobRoleLabel(job.kind),
      state: jobStepState(job.status),
      role: JOB_KIND_ROLE[job.kind] as string | undefined,
    })),
    { key: 'you', label: 'あなた', state: youState, role: undefined },
  ];

  return (
    <div className="agent-timeline">
      <div className="agent-timeline__heading">エージェント・リレー</div>
      <div className="agent-timeline__track">
        {steps.map((step, i) => (
          <span key={step.key} className="agent-timeline__segment">
            {i > 0 && <span className="agent-timeline__arrow" aria-hidden="true" />}
            <span
              className={[
                'agent-timeline__step',
                `agent-timeline__step--${step.state}`,
                step.role !== undefined ? `agent-timeline__step--${step.role}` : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <span className="agent-timeline__dot" aria-hidden="true" />
              {step.label}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
