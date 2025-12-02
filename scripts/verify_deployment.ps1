# NBLBot Pre-Deployment Verification Script for Windows
# Run: .\scripts\verify_deployment.ps1

Write-Host "NBLBot Pre-Deployment Verification" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

$Errors = 0
$Warnings = 0

function Print-Status($success, $message) {
    if ($success) {
        Write-Host "OK $message" -ForegroundColor Green
    } else {
        Write-Host "FAIL $message" -ForegroundColor Red
        $script:Errors++
    }
}

function Print-Warning($message) {
    Write-Host "WARN $message" -ForegroundColor Yellow
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
        Print-Status $false "Python 3.10+ required"
    }
} catch {
    Print-Status $false "Python not found"
}

# 2. Check .env file
Write-Host ""
Write-Host "2. Checking environment configuration..."
if (Test-Path ".env") {
    Print-Status $true ".env file exists"
    
    $envContent = Get-Content .env -Raw
    $requiredVars = @("DISCORD_TOKEN", "APPLICATION_ID", "SUPABASE_URL", "SUPABASE_KEY")
    foreach ($var in $requiredVars) {
        if ($envContent -match "$var=.+" -and $envContent -notmatch "$var=your_") {
            Print-Status $true "$var is set"
        } else {
            Print-Status $false "$var not configured"
        }
    }
} else {
    Print-Status $false ".env file missing"
}

# 3. Check virtual environment
Write-Host ""
Write-Host "3. Checking virtual environment..."
if (Test-Path ".venv") {
    Print-Status $true "Virtual environment exists"
} else {
    Print-Warning "Virtual environment not found"
}

# 4. Check dependencies
Write-Host ""
Write-Host "4. Checking dependencies..."
if (Test-Path "requirements.txt") {
    Print-Status $true "requirements.txt exists"
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

# 6. Check git security
Write-Host ""
Write-Host "6. Checking git security..."
if (Test-Path ".git") {
    if (Test-Path ".gitignore") {
        $gitignore = Get-Content .gitignore -Raw
        if ($gitignore -match "\.env") {
            Print-Status $true ".env is in .gitignore"
        } else {
            Print-Warning ".env not in .gitignore"
        }
    }
} else {
    Print-Warning "Not a git repository"
}

# Summary
Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Verification Summary" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

if ($Errors -eq 0 -and $Warnings -eq 0) {
    Write-Host "All checks passed! Ready for production." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "1. Review docs\PRODUCTION_CHECKLIST.md"
    Write-Host "2. Follow docs\ORACLE_CLOUD_DEPLOYMENT.md"
    Write-Host "3. Test health check endpoint"
    exit 0
} elseif ($Errors -eq 0) {
    Write-Host "$Warnings warning(s) found." -ForegroundColor Yellow
    Write-Host "You can proceed but review warnings."
    exit 0
} else {
    Write-Host "$Errors error(s) and $Warnings warning(s) found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix errors before deploying."
    exit 1
}
