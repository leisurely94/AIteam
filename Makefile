PY ?= python3
GOAL ?= 为质量团队做一个周报自动汇总工具：拉取任务与缺陷数据，生成周报并支持导出

.PHONY: help test demo run chat talk team memory status export roles clean

help:
	@echo "make test    运行全部单元测试"
	@echo "make demo    离线跑一次完整流程"
	@echo "make run     执行 GOAL 变量指定的目标"
	@echo "make chat    进入主入口对话"
	@echo "make talk    与某个数字人对话（WHO=姓名）"
	@echo "make team    查看团队名片与形象"
	@echo "make memory  查看某个数字人的记忆（WHO=姓名）"
	@echo "make export  导出最近一次运行的交付包"
	@echo "make clean   清理运行数据与构建产物"

test:
	PYTHONPATH=. $(PY) -m unittest discover -s tests -v

demo:
	$(PY) -m ai_team demo

run:
	$(PY) -m ai_team run "$(GOAL)" --report

chat:
	$(PY) -m ai_team chat

talk:
	$(PY) -m ai_team talk "$(WHO)"

team:
	$(PY) -m ai_team team

memory:
	$(PY) -m ai_team memory "$(WHO)"

status:
	$(PY) -m ai_team status

export:
	$(PY) -m ai_team export latest --out dist

roles:
	$(PY) -m ai_team roles

clean:
	rm -rf .ai_team dist build *.egg-info __pycache__ */__pycache__
