.PHONY: help test lint run-control run-poll run-agent build-console review-export review-report

help:
	@echo "test           控制面测试"
	@echo "lint           ruff 检查"
	@echo "run-control    本地起控制面（需要 SKEIN_DB）"
	@echo "run-poll       本地起采集循环（需要论坛只读库）"
	@echo "run-agent      本地起节点代理（需要 worker.env）"
	@echo "build-console  构建控制台"

test:
	cd services/control && python -m pytest -q

lint:
	cd services/control && ruff check src tests

run-control:
	cd services/control && python -m skein

run-poll:
	cd services/control && python -m skein.poll

review-export:
	cd services/control && python -m skein.review export --out labels.csv

review-report:
	cd services/control && python -m skein.review report --labels labels.csv

run-agent:
	cd services/node-agent && node node-agent.mjs

build-console:
	cd services/console && npm ci && npm run build
