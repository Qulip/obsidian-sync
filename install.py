#!/usr/bin/env python3
"""
install.py — knowledge-management skill 설치 및 환경변수 설정

사용법:
  python install.py

지원 에이전트: Claude Code, Gemini CLI, Codex CLI, Antigravity, Cursor, Windsurf
지원 OS: macOS, Linux, Windows
의존성: Python 3 표준 라이브러리만 사용
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

SKILL_NAME = 'knowledge-management'
SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_SOURCE = SCRIPT_DIR / 'SKILLS' / SKILL_NAME

ENV_VARS: list[tuple[str, str]] = [
    ('OBSIDIAN_SYNC_URL', 'obsidian-sync API 주소 (예: http://localhost:8000)'),
    ('KNOWLEDGE_API_TOKEN', 'DB API token. Admin token은 사용하지 마세요.'),
]

# global_dir=None 이면 프로젝트 단위 설치만 지원
AGENTS: dict[str, dict] = {
    '1': {'name': 'Claude Code', 'global_dir': '~/.claude/skills'},
    '2': {'name': 'Gemini CLI', 'global_dir': '~/.gemini/skills'},
    '3': {'name': 'Codex CLI (OpenAI)', 'global_dir': '~/.codex/skills'},
    '4': {'name': 'Antigravity', 'global_dir': '~/.gemini/antigravity/skills'},
    '5': {'name': 'Cursor', 'global_dir': '~/.cursor/skills'},  # 프로젝트 단위 전용
    '6': {'name': 'Windsurf', 'global_dir': '~/.windsurf/skills'},
}

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    _header('knowledge-management skill 설치')

    if not SKILL_SOURCE.exists():
        _err(f'스킬 소스를 찾을 수 없습니다: {SKILL_SOURCE}')
        _err('install.py를 프로젝트 루트 디렉터리에서 실행하세요.')
        return 1
    if not (SKILL_SOURCE / 'SKILL.md').exists():
        _err(f'스킬 메타 파일을 찾을 수 없습니다: {SKILL_SOURCE / "SKILL.md"}')
        return 1

    # 1. 에이전트 선택
    selected = _select_agents()
    if not selected:
        print('선택된 에이전트가 없습니다. 종료합니다.')
        return 0

    print()

    # 2. 스킬 설치
    installed: list[str] = []
    for agent in selected:
        result = _install_skill(agent)
        if result:
            installed.append(result)

    print()

    # 3. 환경변수 수집
    env_values = _collect_env_vars()

    print()

    # 4. 환경변수 적용
    if _confirm_env_persistence(env_values):
        _apply_env_vars(env_values)
    else:
        _print_runtime_env_guidance(env_values)

    # 5. 요약
    _print_summary(installed, env_values)
    return 0


# ---------------------------------------------------------------------------
# 에이전트 선택
# ---------------------------------------------------------------------------


def _select_agents() -> list[dict]:
    print('설치할 코딩 에이전트를 선택하세요 (복수 선택: 쉼표로 구분)\n')
    for key, agent in AGENTS.items():
        scope = '프로젝트 전용' if agent['global_dir'] is None else '글로벌'
        print(f'  {key}. {agent["name"]:<24} ({scope})')
    print()

    raw = input('선택 > ').strip()
    if not raw:
        return []

    keys = [k.strip() for k in raw.split(',')]
    selected: list[dict] = []
    for k in keys:
        if k in AGENTS:
            selected.append(AGENTS[k])
        else:
            print(f'  ⚠  알 수 없는 선택: {k} (무시됨)')

    return selected


# ---------------------------------------------------------------------------
# 스킬 설치
# ---------------------------------------------------------------------------


def _install_skill(agent: dict) -> str | None:
    name = agent['name']

    if agent['global_dir'] is None:
        # Cursor: 프로젝트 경로 직접 입력
        print(f'\n[{name}] 프로젝트 단위 설치만 지원합니다.')
        raw = input('  스킬을 설치할 프로젝트 경로 (비워두면 건너뜀) > ').strip()
        if not raw:
            print(f'  ⏭  {name} 건너뜀')
            return None
        target = Path(raw).expanduser().resolve() / '.cursor' / 'skills' / SKILL_NAME
    else:
        target = Path(agent['global_dir']).expanduser() / SKILL_NAME

    try:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SKILL_SOURCE, target)
        print(f'  ✔ {name}: {target}')
        return str(target)
    except OSError as exc:
        _err(f'{name} 설치 실패: {exc}')
        return None


# ---------------------------------------------------------------------------
# 환경변수 수집
# ---------------------------------------------------------------------------


def _collect_env_vars() -> dict[str, str]:
    print('환경변수를 입력하세요. 빈값으로 두면 나중에 직접 설정할 수 있습니다.\n')
    values: dict[str, str] = {}
    for var, desc in ENV_VARS:
        current = os.environ.get(var, '')
        hint = f'현재값: {current}  ' if current else ''
        val = input(f'  {var}\n  {desc}\n  {hint}> ').strip()
        values[var] = val if val else current
        print()
    return values


# ---------------------------------------------------------------------------
# 환경변수 적용
# ---------------------------------------------------------------------------


def _apply_env_vars(env_values: dict[str, str]) -> None:
    to_set = {k: v for k, v in env_values.items() if v}
    skipped = [k for k, v in env_values.items() if not v]

    if to_set:
        if platform.system() == 'Windows':
            _apply_windows(to_set)
        else:
            _apply_unix(to_set)

    if skipped:
        print()
        print('  ⚠  아래 환경변수는 설정되지 않았습니다. 나중에 직접 추가하세요:')
        shell = _detect_shell()
        for var in skipped:
            if shell == 'fish':
                print(f'       set -x {var} "<값>"')
            elif platform.system() == 'Windows':
                print(f'       setx {var} "<값>"')
            else:
                print(f'       export {var}="<값>"')


def _confirm_env_persistence(env_values: dict[str, str]) -> bool:
    if not any(env_values.values()):
        return False

    print('환경변수 저장 방식')
    print('  권장: Agent 실행 환경이나 MCP client 설정에서 secret/env로 주입')
    print('  선택: 현재 셸 프로필에 저장 (token이 평문으로 기록됩니다)')
    raw = input('  셸 프로필에 환경변수를 저장할까요? [y/N] > ').strip().lower()
    return raw in {'y', 'yes'}


def _print_runtime_env_guidance(env_values: dict[str, str]) -> None:
    if not any(env_values.values()):
        return

    shell = _detect_shell()
    print()
    print('  환경변수는 저장하지 않았습니다. Agent 실행 환경에 직접 주입하세요:')
    for var, value in env_values.items():
        if not value:
            continue
        display_value = _display_env_value(var, value)
        if shell == 'fish':
            print(f'       set -x {var} "{display_value}"')
        elif platform.system() == 'Windows':
            print(f'       setx {var} "{display_value}"')
        else:
            print(f'       export {var}="{display_value}"')


def _display_env_value(var: str, value: str) -> str:
    if 'TOKEN' in var or 'SECRET' in var:
        return '<입력한 값을 secret/env로 주입>'
    return value


def _apply_unix(values: dict[str, str]) -> None:
    shell = _detect_shell()
    profile = _unix_profile(shell)

    lines = ['\n# knowledge-management skill']
    for k, v in values.items():
        if shell == 'fish':
            lines.append(f'set -x {k} "{v}"')
        else:
            lines.append(f'export {k}="{v}"')

    profile.parent.mkdir(parents=True, exist_ok=True)
    with open(profile, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'  ✔ 환경변수 → {profile}')
    print(f'     적용하려면: source {profile}')


def _apply_windows(values: dict[str, str]) -> None:
    failed: dict[str, str] = {}
    for k, v in values.items():
        try:
            subprocess.run(['setx', k, v], check=True, capture_output=True)
            print(f'  ✔ 환경변수 설정: {k}')
        except subprocess.CalledProcessError, FileNotFoundError:
            failed[k] = v

    if failed:
        # setx 없는 환경(MinGW 등) — PowerShell 프로파일에 기록
        _apply_powershell(failed)


def _apply_powershell(values: dict[str, str]) -> None:
    profile = (
        Path.home() / 'Documents' / 'PowerShell' / 'Microsoft.PowerShell_profile.ps1'
    )
    profile.parent.mkdir(parents=True, exist_ok=True)
    lines = ['\n# knowledge-management skill']
    for k, v in values.items():
        lines.append(f'$env:{k} = "{v}"')
    with open(profile, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'  ✔ 환경변수 → {profile}')


def _detect_shell() -> str:
    shell = os.environ.get('SHELL', '')
    if 'zsh' in shell:
        return 'zsh'
    if 'fish' in shell:
        return 'fish'
    return 'bash'


def _unix_profile(shell: str) -> Path:
    home = Path.home()
    if shell == 'zsh':
        return home / '.zshrc'
    if shell == 'fish':
        return home / '.config' / 'fish' / 'config.fish'
    candidate = home / '.bashrc'
    return candidate if candidate.exists() else home / '.bash_profile'


# ---------------------------------------------------------------------------
# 출력 유틸
# ---------------------------------------------------------------------------


def _header(title: str) -> None:
    bar = '=' * 56
    print(f'\n{bar}')
    print(f'  {title}')
    print(f'{bar}\n')


def _err(msg: str) -> None:
    print(f'  ✘ {msg}', file=sys.stderr)


def _print_summary(installed: list[str], env_values: dict[str, str]) -> None:
    print()
    _header('설치 완료')

    print('  [스킬 설치 경로]')
    if installed:
        for path in installed:
            print(f'    • {path}')
    else:
        print('    (없음)')

    print()
    print('  [환경변수]')
    for k, v in env_values.items():
        status = '✔ 입력됨 ' if v else '⚠  미입력'
        print(f'    {status}  {k}')

    if any(not v for v in env_values.values()):
        print()
        print('  미설정 환경변수를 추가한 뒤 스킬을 사용할 수 있습니다.')

    if env_values.get('OBSIDIAN_SYNC_URL'):
        print()
        print('  [MCP client 설정]')
        print(f'    endpoint: {env_values["OBSIDIAN_SYNC_URL"].rstrip("/")}/mcp')
        print('    header: Authorization: Bearer $KNOWLEDGE_API_TOKEN')


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n\n취소되었습니다.')
        raise SystemExit(0) from None
