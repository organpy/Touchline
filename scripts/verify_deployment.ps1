# NBLBot Pre-Deployment Verification Script (Windows)
# Run this before deploying to production: .\scripts\verify_deployment.ps1

Write-Host "🚀 NBLBot Pre-Deployment Verification" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

$Errors = 0
$Warnings = 0

function Print-Status($success, $message) {
    if ($success) {
        Write-Host "✅ $message" -ForegroundColor Green
    } else {
        Write-Host "❌ $message" -ForegroundColor Red
        $script:Errors++
    }
}

function Print-Warning($message) {
    Write-Host "⚠️  $message" -ForegroundColor Yellow
    $script:Warnings++
}

# 1. Check Python version
Write-Host "1. Checking Python version..."
try {
    $pythonVersion = python --version 2>&1
    $version = [version]($pythonVersion -replace 'Python ', '')
    if ($version.Major -ge 3 -and $version.Minor -ge 10) {
        Print-Status $true "Python version: $pythonVersion"
    } else {
        Print-Status $false "Python 3.10+ required, found: $pythonVersion"
    }
} catch {
    Print-Status $false "Python not found in PATH"
}

# 2. Check if .env exists
Write-Host ""
Write-Host "2. Checking environment configuration..."
if (Test-Path ".env") {
    Print-Status $true ".env file exists"
    
    # Check required variables
    $envContent = Get-Content .env -Raw
    $requiredVars = @("DISCORD_TOKEN", "APPLICATION_ID", "SUPABASE_URL", "SUPABASE_KEY")
    foreach ($var in $requiredVars) {
        if ($envContent -match "$var=.+" -and $envContent -notmatch "$var=your_") {
            Print-Status $true "$var is set"
        } else {
            Print-Status $false "$var not configured in .env"
        }
    }
} else {
    Print-Status $false ".env file missing (copy from .env.sample)"
}

# 3. Check virtual environment
Write-Host ""
Write-Host "3. Checking virtual environment..."
if (Test-Path ".venv") {
    Print-Status $true "Virtual environment exists"
} else {
    Print-Warning "Virtual environment not found (.venv)"
}

# 4. Check dependencies
Write-Host ""
Write-Host "4. Checking dependencies..."
if (Test-Path "requirements.txt") {
    Print-Status $true "requirements.txt exists"
    
    # Try to import key packages
    try {
        python -c "import discord" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Print-Status $true "discord.py installed"
        } else {
            Print-Status $false "discord.py not installed"
        }
    } catch {
        Print-Status $false "discord.py not installed"
    }
    
    try {
        python -c "import supabase" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Print-Status $true "supabase installed"
        } else {
            Print-Status $false "supabase not installed"
        }
    } catch {
        Print-Status $false "supabase not installed"
    }
} else {
    Print-Status $false "requirements.txt missing"
}

# 5. Check critical files
Write-Host ""
Write-Host "5. Checking critical files..."
$criticalFiles = @("bot.py", "utils\health_check.py", "utils\rate_limiter.py", "db\queries.py")
foreach ($file in $criticalFiles) {
    if (Test-Path $file) {
        Print-Status $true "$file exists"
    } else {
        Print-Status $false "$file missing"
    }
}

# 6. Check for secrets in git
Write-Host ""
Write-Host "6. Checking git security..."
if (Test-Path ".git") {
    # Check if .env is in .gitignore
    if (Test-Path ".gitignore") {
        $gitignore = Get-Content .gitignore -Raw
        if ($gitignore -match "\.env") {
            Print-Status $true ".env is in .gitignore"
        } else {
            Print-Warning ".env not in .gitignore"
        }
    }
    
    # Check if .env is in git history
    $gitLog = git log --all --full-history -- .env 2>&1
    if ($gitLog -match "commit") {
        Print-Status $false ".env found in git history (SECURITY RISK!)"
    } else {
        Print-Status $true ".env not in git history"
    }
} else {
    Print-Warning "Not a git repository"
}

# 7. Syntax check
Write-Host ""
Write-Host "7. Checking Python syntax..."
try {
    python -m py_compile bot.py 2>$null
    if ($LASTEXITCODE -eq 0) {
        Print-Status $true "bot.py syntax valid"
    } else {
        Print-Status $false "bot.py has syntax errors"
    }
} catch {
    Print-Status $false "bot.py has syntax errors"
}

# 8. Run tests
Write-Host ""
Write-Host "8. Running tests..."
try {
    $pytestExists = Get-Command pytest -ErrorAction SilentlyContinue
    if ($pytestExists) {
        pytest test/ -q --tb=no 2>$null
        if ($LASTEXITCODE -eq 0) {
            Print-Status $true "All tests passing"
        } else {
            Print-Status $false "Some tests failing (run: pytest test/ -v)"
        }
    } else {
        Print-Warning "pytest not installed (run: pip install pytest)"
    }
} catch {
    Print-Warning "pytest not installed"
}

# 9. Check documentation
Write-Host ""
Write-Host "9. Checking documentation..."
$docs = @("docs\ORACLE_CLOUD_DEPLOYMENT.md", "docs\PRODUCTION_CHECKLIST.md", "docs\PRODUCTION_READINESS.md")
foreach ($doc in $docs) {
    if (Test-Path $doc) {
        Print-Status $true "$doc exists"
    } else {
        Print-Warning "$doc missing"
    }
}

# Summary
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "📊 Verification Summary" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

if ($Errors -eq 0 -and $Warnings -eq 0) {
    Write-Host "🎉 All checks passed! Ready for production." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "1. Review docs\PRODUCTION_CHECKLIST.md"
    Write-Host "2. Follow docs\ORACLE_CLOUD_DEPLOYMENT.md"
    Write-Host "3. Test health check: curl http://localhost:8080/health"
    exit 0
} elseif ($Errors -eq 0) {
    Write-Host "⚠️  $Warnings warning(s) found. Review before deploying." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "You can proceed, but address warnings if possible."
    exit 0
} else {
    Write-Host "❌ $Errors error(s) and $Warnings warning(s) found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix errors before deploying to production."
    Write-Host ""
    Write-Host "Common fixes:"
    Write-Host "- Copy .env.sample to .env and configure"
    Write-Host "- Run: pip install -r requirements.txt"
    Write-Host "- Run: pytest test -v"
    exit 1
}
