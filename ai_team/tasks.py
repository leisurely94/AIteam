"""任务图：校验、拓扑排序、就绪队列。

只关心「依赖是否成立」和「谁是下一个」，不负责执行。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from .models import Task


class PlanError(ValueError):
    """任务计划不合法（重复 id、依赖缺失、存在环）。"""


class TaskGraph:
    def __init__(self, tasks: Sequence[Task]):
        self.tasks: Dict[str, Task] = {}
        for task in tasks:
            if not task.id:
                raise PlanError("任务缺少 id")
            if task.id in self.tasks:
                raise PlanError("任务 id 重复：%s" % task.id)
            self.tasks[task.id] = task
        if not self.tasks:
            raise PlanError("任务计划为空")
        self._incep: Dict[str, List[str]] = {}
        self._succ: Dict[str, List[str]] = {tid: [] for tid in self.tasks}
        self._validate()
        self.order = self._topological_order()

    # ---------- 校验 ----------

    def _validate(self) -> None:
        for task in self.tasks.values():
            deps = list(dict.fromkeys(task.depends_on))
            task.depends_on = deps
            for dep in deps:
                if dep == task.id:
                    raise PlanError("任务 %s 依赖了自己" % task.id)
                if dep not in self.tasks:
                    raise PlanError("任务 %s 依赖不存在的任务 %s" % (task.id, dep))
                self._succ[dep].append(task.id)
            self._incep[task.id] = deps

    def _topological_order(self) -> List[str]:
        indegree = {tid: len(deps) for tid, deps in self._incep.items()}
        ready = [tid for tid in self.tasks if indegree[tid] == 0]
        ordered: List[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for nxt in self._succ[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if len(ordered) != len(self.tasks):
            unresolved = [tid for tid, deg in indegree.items() if deg > 0]
            raise PlanError("任务依赖存在环：%s" % "、".join(unresolved))
        return ordered

    # ---------- 查询 ----------

    def get(self, task_id: str) -> Task:
        return self.tasks[task_id]

    def dependents(self, task_id: str) -> List[str]:
        return list(self._succ.get(task_id, []))

    def upstream_ids(self, task_id: str) -> List[str]:
        return list(self._incep.get(task_id, []))

    def ready(self) -> List[Task]:
        """依赖已全部 done 的 pending 任务，按拓扑顺序返回。"""
        done = {tid for tid, t in self.tasks.items() if t.status == "done"}
        result = []
        for tid in self.order:
            task = self.tasks[tid]
            if task.status != "pending":
                continue
            if all(dep in done for dep in task.depends_on):
                result.append(task)
        return result

    def pending(self) -> List[Task]:
        return [self.tasks[tid] for tid in self.order if self.tasks[tid].status == "pending"]

    def unfinished(self) -> bool:
        return any(t.status in ("pending", "running") for t in self.tasks.values())

    def stalled(self) -> List[Task]:
        """既不是 done/failed/blocked，也没有就绪条件满足的任务（通常依赖失败）。"""
        ready_ids = {t.id for t in self.ready()}
        return [
            self.tasks[tid]
            for tid in self.order
            if self.tasks[tid].status == "pending" and tid not in ready_ids
        ]

    def progress(self) -> Dict[str, int]:
        counts = {"total": len(self.tasks), "done": 0, "failed": 0, "blocked": 0, "pending": 0}
        for task in self.tasks.values():
            if task.status in counts:
                counts[task.status] += 1
            elif task.status == "running":
                counts["pending"] += 1
        return counts

    def layers(self) -> List[List[str]]:
        """按依赖层级分组，为后续并行执行预留。"""
        depth: Dict[str, int] = {}
        for tid in self.order:
            deps = self._incep[tid]
            depth[tid] = 0 if not deps else max(depth[d] for d in deps) + 1
        max_depth = max(depth.values()) if depth else 0
        return [[tid for tid in self.order if depth[tid] == d] for d in range(max_depth + 1)]

    def block_downstream(self, task_id: str, reason: str = "") -> List[str]:
        """上游失败/阻塞时，把下游全部标记 blocked（递归）。"""
        blocked: List[str] = []
        stack: Iterable[str] = [task_id]
        seen = set()
        while stack:
            current = stack.pop()
            for nxt in self._succ.get(current, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                task = self.tasks[nxt]
                if task.status == "pending":
                    task.status = "blocked"
                    if reason:
                        task.review = {"verdict": "blocked", "summary": reason, "issues": []}
                    blocked.append(nxt)
                stack.append(nxt)
        return blocked
