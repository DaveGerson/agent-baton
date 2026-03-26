# Agent Baton — Claude Code — Windows Install
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

param(
    [ValidateSet("user", "project", "")]
    [string]$Scope = "",
    [switch]$Upgrade
)

Write-Host ""
Write-Host "  Agent Baton — Claude Code" -ForegroundColor Cyan
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$AgentsDir  = Join-Path $RootDir "agents"
$RefsDir    = Join-Path $RootDir "references"
$SkillsSrc  = Join-Path $RootDir "templates" "skills"
$ClaudeMd   = Join-Path $RootDir "templates" "CLAUDE.md"
$SettingsJ  = Join-Path $RootDir "templates" "settings.json"

if (-not (Test-Path $AgentsDir)) {
    Write-Host "  Error: agents/ not found. Run from the skill folder." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
function Test-Prerequisites {
    # Check Python
    $py = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $py) {
        $py = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $py) {
        Write-Error "Python 3.10+ is required but not found in PATH."
        Write-Error "Install from https://python.org"
        exit 1
    }
    $pyVersion = & $py.Source --version 2>&1
    if ($pyVersion -match "(\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            Write-Error "Python 3.10+ required (found: $pyVersion)"
            exit 1
        }
    }

    # Check git
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Error "git is required but not found in PATH."
        Write-Error "Install from https://git-scm.com"
        exit 1
    }
}

Test-Prerequisites

# ── Step 1: Scope ──────────────────────────────────────────
if ($Scope -eq "") {
    Write-Host "  STEP 1: Install Location"
    Write-Host "  ────────────────────────"
    Write-Host "    1) User-level   (~/.claude/)  — all projects"
    Write-Host "    2) Project-level (.claude/)   — current project only"
    Write-Host ""
    $choice = Read-Host "  Choose [1/2]"
    $Scope = if ($choice -eq "1") { "user" } elseif ($choice -eq "2") { "project" } else {
        Write-Host "Invalid choice." -ForegroundColor Red; exit 1
    }
}

if ($Scope -eq "user") { $Base = Join-Path $env:USERPROFILE ".claude" }
else { $Base = ".claude" }

$AgentTarget = Join-Path $Base "agents"
$RefTarget   = Join-Path $Base "references"
$TeamCtx     = Join-Path $Base "team-context"
$KnowledgeDir = Join-Path $Base "knowledge"
$SkillsDir   = Join-Path $Base "skills"

# Test write permissions
try {
    $testFile = Join-Path $Base ".write-test"
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    [System.IO.File]::WriteAllText($testFile, "test")
    Remove-Item $testFile -Force
} catch {
    Write-Error "Cannot write to $Base — check folder permissions."
    Write-Error "If you don't have admin access, use project-level install (option 2)."
    exit 1
}

# ── Step 2: Install Core Files ─────────────────────────────
Write-Host ""
Write-Host "  STEP 2: Installing Core Files" -ForegroundColor Cyan
Write-Host "  ─────────────────────────────"

New-Item -ItemType Directory -Force -Path $AgentTarget | Out-Null
$agentCount = 0
Get-ChildItem "$AgentsDir\*.md" | ForEach-Object {
    Copy-Item $_.FullName -Destination $AgentTarget -Force
    Write-Host "  + Agent:     $($_.Name)" -ForegroundColor Green
    $agentCount++
}

New-Item -ItemType Directory -Force -Path $RefTarget | Out-Null
$refCount = 0
Get-ChildItem "$RefsDir\*.md" | ForEach-Object {
    Copy-Item $_.FullName -Destination $RefTarget -Force
    Write-Host "  + Reference: $($_.Name)" -ForegroundColor Green
    $refCount++
}

New-Item -ItemType Directory -Force -Path $TeamCtx | Out-Null
New-Item -ItemType Directory -Force -Path $KnowledgeDir | Out-Null
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null

# Install skills from templates/skills/
$skillCount = 0
if (Test-Path $SkillsSrc) {
    Get-ChildItem -Path $SkillsSrc -Directory | ForEach-Object {
        $targetSkillDir = Join-Path $SkillsDir $_.Name
        New-Item -ItemType Directory -Force -Path $targetSkillDir | Out-Null
        Copy-Item (Join-Path $_.FullName "*") -Destination $targetSkillDir -Force
        Write-Host "  + Skill:     $($_.Name)" -ForegroundColor Green
        $skillCount++
    }
}

Write-Host "  + Dirs:      team-context/, knowledge/, skills/" -ForegroundColor Green

# CLAUDE.md — skip on upgrade, but merge identity block if missing
if ($Upgrade) {
    # Find the existing CLAUDE.md
    $existingClaudeMd = $null
    if ($Scope -eq "project" -and (Test-Path "CLAUDE.md")) {
        $existingClaudeMd = "CLAUDE.md"
    } elseif ($Scope -eq "user") {
        $userClaudeMd = Join-Path $Base "CLAUDE.md"
        if (Test-Path $userClaudeMd) { $existingClaudeMd = $userClaudeMd }
    }

    if ($existingClaudeMd -and -not (Select-String -Path $existingClaudeMd -Pattern "What is Agent Baton" -Quiet)) {
        # Merge the identity block into the existing CLAUDE.md
        $py = Get-Command python3 -ErrorAction SilentlyContinue
        if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
        if ($py) {
            try {
                $mergeIdentity = @"
import sys
identity = '''## What is Agent Baton?

Agent Baton is an **installed Python CLI tool** (``baton``) that orchestrates
multi-agent execution plans for Claude Code. It is a local command-line
program - not a concept or methodology. Run ``baton --help`` to see all
available commands. The core workflow is: ``baton plan`` generates a phased
execution plan with agent assignments, risk assessment, and QA gates;
``baton execute`` drives that plan step-by-step, dispatching specialist
agents, running gates, and recording results. All state is persisted to
``.claude/team-context/`` so sessions can crash and resume. Use ``/baton-help``
for the full CLI reference.

'''
with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')
insert_at = 0
for i, line in enumerate(lines):
    if line.startswith('# '):
        insert_at = i + 1
        if insert_at < len(lines) and lines[insert_at].strip() == '':
            insert_at += 1
        break
lines.insert(insert_at, identity)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
"@
                & $py.Source -c $mergeIdentity $existingClaudeMd 2>$null
                Write-Host "  + CLAUDE.md:  identity block merged" -ForegroundColor Green
            } catch {
                Write-Host "  ! CLAUDE.md identity merge failed - add manually" -ForegroundColor Yellow
            }
        } else {
            Write-Host "  ! CLAUDE.md identity merge skipped (no Python)" -ForegroundColor Yellow
        }
    } elseif ($existingClaudeMd) {
        Write-Host "  ~ CLAUDE.md:  identity block present (upgrade mode)" -ForegroundColor Yellow
    } else {
        Write-Host "  ~ CLAUDE.md:  not found (upgrade mode)" -ForegroundColor Yellow
    }
} else {
    if (Test-Path $ClaudeMd) {
        if ($Scope -eq "project") {
            if (Test-Path "CLAUDE.md") {
                Write-Host "  ! CLAUDE.md exists — merge manually from: $ClaudeMd" -ForegroundColor Yellow
            } else {
                Copy-Item $ClaudeMd -Destination "CLAUDE.md" -Force
                Write-Host "  + CLAUDE.md copied to project root" -ForegroundColor Green
            }
        } elseif ($Scope -eq "user") {
            $userClaudeMd = Join-Path $Base "CLAUDE.md"
            if (Test-Path $userClaudeMd) {
                Write-Host "  ! ~/.claude/CLAUDE.md exists — merge manually" -ForegroundColor Yellow
            } else {
                Copy-Item $ClaudeMd -Destination $userClaudeMd -Force
                Write-Host "  + CLAUDE.md copied to ~/.claude/" -ForegroundColor Green
            }
        }
    }
}

# settings.json (hooks) — merge on upgrade, copy on fresh install
if (Test-Path $SettingsJ) {
    if ($Scope -eq "project") {
        $settingsPath = ".claude\settings.json"
    } else {
        $settingsPath = Join-Path $Base "settings.json"
    }

    if (Test-Path $settingsPath) {
        # Merge hooks from template into existing settings
        $py = Get-Command python3 -ErrorAction SilentlyContinue
        if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
        if ($py) {
            try {
                $mergeScript = @"
import json, sys
src = json.loads(open(sys.argv[1]).read())
dst = json.loads(open(sys.argv[2]).read())
src_hooks = src.get('hooks', {})
dst_hooks = dst.get('hooks', {})
for event, src_entries in src_hooks.items():
    existing = dst_hooks.get(event, [])
    existing_cmds = {e.get('command','') for e in existing if isinstance(e,dict)}
    for entry in src_entries:
        cmd = entry.get('command','') if isinstance(entry,dict) else ''
        if cmd not in existing_cmds:
            existing.append(entry)
            existing_cmds.add(cmd)
    dst_hooks[event] = existing
dst['hooks'] = dst_hooks
open(sys.argv[2], 'w').write(json.dumps(dst, indent=2) + '\n')
"@
                & $py.Source -c $mergeScript $SettingsJ $settingsPath 2>$null
                Write-Host "  + Hooks:     settings.json merged" -ForegroundColor Green
            } catch {
                Write-Host "  ! settings.json merge failed — merge hooks manually from: $SettingsJ" -ForegroundColor Yellow
            }
        } else {
            Write-Host "  ! settings.json merge failed (no Python) — merge manually" -ForegroundColor Yellow
        }
    } else {
        Copy-Item $SettingsJ -Destination $settingsPath -Force
        Write-Host "  + Hooks:     settings.json installed" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "  Installed: $agentCount agents + $refCount references + $skillCount skills" -ForegroundColor Green

# ── Step 3: MCP / Knowledge Infrastructure ─────────────────
Write-Host ""
Write-Host "  STEP 3: Knowledge Infrastructure (Optional)" -ForegroundColor Cyan
Write-Host "  ────────────────────────────────────────────"
Write-Host ""
Write-Host "  How will agents access domain knowledge?"
Write-Host ""
Write-Host "    1) Knowledge packs only (local files — no infrastructure needed)"
Write-Host "    2) Databricks MCP (Vector Search, SQL, Genie — recommended for enterprise)"
Write-Host "    3) Local RAG via mcp-local-rag (requires Node.js)"
Write-Host "    4) Skip for now (configure later)"
Write-Host ""
$mcpChoice = Read-Host "  Choose [1/2/3/4]"

switch ($mcpChoice) {
    "1" {
        Write-Host ""
        Write-Host "  Knowledge packs selected." -ForegroundColor Green
        Write-Host "  Create domain knowledge files in: $KnowledgeDir" -ForegroundColor White
        Write-Host "  Use the talent-builder agent to generate them from documentation." -ForegroundColor White
        Write-Host ""
        Write-Host "  Prompt to create your first knowledge pack:" -ForegroundColor Yellow
        Write-Host '  "Spawn the talent-builder agent. Onboard [DOMAIN] as a domain."' -ForegroundColor White
    }
    "2" {
        Write-Host ""
        Write-Host "  Databricks MCP selected." -ForegroundColor Green
        Write-Host ""
        Write-Host "  ── Databricks Configuration ──" -ForegroundColor Cyan
        $dbHost = Read-Host "  Databricks workspace URL (e.g., https://your-org.cloud.databricks.com)"
        Write-Host "  Personal Access Token:" -ForegroundColor White
        Write-Host "  Set the DATABRICKS_TOKEN environment variable with your PAT." -ForegroundColor Yellow
        Write-Host "  Example: `$env:DATABRICKS_TOKEN = 'dapi...'" -ForegroundColor DarkGray
        Write-Host ""
        $dbTokenRef = "`$env:DATABRICKS_TOKEN"
        $dbCatalog = Read-Host "  Catalog name (e.g., main)"
        $dbSchema = Read-Host "  Schema name (e.g., knowledge)"

        Write-Host ""
        Write-Host "  Which Databricks MCP servers do you want to connect?" -ForegroundColor White
        Write-Host "    a) Vector Search only (document RAG)"
        Write-Host "    b) Vector Search + SQL"
        Write-Host "    c) Vector Search + SQL + Genie"
        Write-Host "    d) All of the above + UC Functions"
        $dbServers = Read-Host "  Choose [a/b/c/d]"

        # Build MCP config
        $mcpConfig = @{}
        
        # Vector Search (always included)
        $vsUrl = "$dbHost/api/2.0/mcp/vector-search/$dbCatalog/$dbSchema"
        $mcpConfig["databricks-vector-search"] = @{
            type = "streamable-http"
            url = $vsUrl
            headers = @{ Authorization = "Bearer $dbTokenRef" }
        }
        Write-Host "  + Vector Search: $vsUrl" -ForegroundColor Green

        if ($dbServers -in @("b", "c", "d")) {
            $sqlUrl = "$dbHost/api/2.0/mcp/sql"
            $mcpConfig["databricks-sql"] = @{
                type = "streamable-http"
                url = $sqlUrl
                headers = @{ Authorization = "Bearer $dbTokenRef" }
            }
            Write-Host "  + SQL: $sqlUrl" -ForegroundColor Green
        }

        if ($dbServers -in @("c", "d")) {
            $genieId = Read-Host "  Genie Space ID"
            $genieUrl = "$dbHost/api/2.0/mcp/genie/$genieId"
            $mcpConfig["databricks-genie"] = @{
                type = "streamable-http"
                url = $genieUrl
                headers = @{ Authorization = "Bearer $dbTokenRef" }
            }
            Write-Host "  + Genie: $genieUrl" -ForegroundColor Green
        }

        if ($dbServers -eq "d") {
            $funcSchema = Read-Host "  UC Functions schema (e.g., main/operations)"
            $funcUrl = "$dbHost/api/2.0/mcp/functions/$funcSchema"
            $mcpConfig["databricks-functions"] = @{
                type = "streamable-http"
                url = $funcUrl
                headers = @{ Authorization = "Bearer $dbTokenRef" }
            }
            Write-Host "  + UC Functions: $funcUrl" -ForegroundColor Green
        }

        # Write or merge settings.json
        $settingsPath = if ($Scope -eq "project") { ".claude\settings.json" } else { Join-Path $Base "settings.json" }
        
        if (Test-Path $settingsPath) {
            $existing = Get-Content $settingsPath -Raw | ConvertFrom-Json
            if (-not $existing.mcpServers) {
                $existing | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue @{} -Force
            }
            foreach ($key in $mcpConfig.Keys) {
                $existing.mcpServers | Add-Member -NotePropertyName $key -NotePropertyValue $mcpConfig[$key] -Force
            }
            $existing | ConvertTo-Json -Depth 10 | Set-Content $settingsPath
        } else {
            @{ mcpServers = $mcpConfig } | ConvertTo-Json -Depth 10 | Set-Content $settingsPath
        }

        Write-Host ""
        Write-Host "  MCP servers configured in: $settingsPath" -ForegroundColor Green
        Write-Host ""
        Write-Host "  NEXT: Create your Vector Search index in Databricks." -ForegroundColor Yellow
        Write-Host "  See DATABRICKS-MCP-SETUP.md Part A for step-by-step instructions." -ForegroundColor White
    }
    "3" {
        Write-Host ""
        Write-Host "  Local RAG selected." -ForegroundColor Green
        Write-Host ""
        Write-Host "  Requires Node.js 18+. Install with:" -ForegroundColor White
        Write-Host '  claude mcp add local-rag --scope user -- npx -y mcp-local-rag' -ForegroundColor White
        Write-Host ""
        Write-Host '  Set BASE_DIR to your documents folder in the env config.' -ForegroundColor White
        Write-Host "  See KNOWLEDGE-INFRASTRUCTURE.md for details." -ForegroundColor White
    }
    default {
        Write-Host ""
        Write-Host "  Skipped. Configure later via settings.json or run this script again." -ForegroundColor White
    }
}

# ── Step 4: Central Database ─────────────────────────────
$BatonDir = Join-Path $env:USERPROFILE ".baton"
if (-not (Test-Path $BatonDir)) {
    New-Item -ItemType Directory -Force -Path $BatonDir | Out-Null
    Write-Host ""
    Write-Host "  STEP 4: Central Database" -ForegroundColor Cyan
    Write-Host "  ────────────────────────"
    Write-Host "  + Created: ~/.baton/ (cross-project analytics)" -ForegroundColor Green
    Write-Host "  central.db will be initialized on first 'baton' command." -ForegroundColor White
} else {
    # Check for migration needs
    $centralDb = Join-Path $BatonDir "central.db"
    $pmoDb = Join-Path $BatonDir "pmo.db"
    if (Test-Path $pmoDb) {
        if (-not (Test-Path $centralDb)) {
            Write-Host "  ~ pmo.db found — will be migrated to central.db on next baton command" -ForegroundColor Yellow
        }
    }
    if (Test-Path $centralDb) {
        Write-Host "  ~ central.db exists — will be upgraded on next baton command" -ForegroundColor Yellow
    }
}

# ── Summary ────────────────────────────────────────────────
Write-Host ""
Write-Host "  =====================================" -ForegroundColor Cyan
if ($Upgrade) {
    Write-Host "  Upgrade Complete ($Scope-level)" -ForegroundColor Cyan
} else {
    Write-Host "  Installation Complete ($Scope-level)" -ForegroundColor Cyan
}
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  VERIFY:     Start Claude Code, run /agents"
Write-Host "  MCP CHECK:  Run /mcp to verify MCP servers"
Write-Host "  FIRST RUN:  'Use the orchestrator to [describe task]'"
Write-Host ""
