@echo off
set AGENT_PROJECT_ID=minesweeper
python project_orchestrator.py --project "윈도우 스타일 지뢰찾기 게임 100x100 (Gemini 3 Flash)" --dir "d:\agent-factory\projects\minesweeper" -r "Architect,LogicDeveloper,UIDeveloper" --skip-forge
