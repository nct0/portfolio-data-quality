<#
.SYNOPSIS
    Regenerate the workbook screenshots in assets/ from real pipeline runs, using Microsoft Excel.

.DESCRIPTION
    1. Runs the pipeline on the original three exports, and on those plus the late batch with the demo decisions,
       into a temporary folder.
    2. Opens each report in Excel (read-only, hidden) and stops if Excel had to repair it (Excel writes a repair log
       named error*.xml to the temporary folder). Then copies cell ranges as pictures, so the images show only the
       sheet: no ribbon, window title, user name, file path or taskbar.
    3. Enlarges each picture inside a temporary chart and exports it as PNG, then removes PNG metadata chunks.

    Requires Windows with Microsoft Excel and the project's virtual environment. The clipboard is overwritten.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\capture-screenshots.ps1
#>
param(
    [string]$Python = ".venv\Scripts\python.exe",
    [string]$OutDir = "assets",
    [int]$Width = 1400  # target image width in pixels at 96 dpi; Excel scales the export with the display setting
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$Python = (Resolve-Path $Python).Path
$OutDir = (New-Item -ItemType Directory -Force $OutDir).FullName
$work = Join-Path ([IO.Path]::GetTempPath()) ("order-quality-shots-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $work | Out-Null

function Invoke-Pipeline([string[]]$Arguments) {
    & $Python -m order_quality run @Arguments --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pipeline failed with exit code $LASTEXITCODE" }
}

Invoke-Pipeline @("--input", "data/raw", "--out", "$work\baseline")
Invoke-Pipeline @("--input", "data/raw", "--input", "data/late-batch", "--corrections", "corrections/demo-decisions.csv",
                  "--out", "$work\late-batch")

function Save-RangePicture($Sheet, [string]$Address, [string]$Name, [int]$TargetWidth = $Width) {
    $range = $Sheet.Range($Address)
    [void]$range.CopyPicture(1, -4147)  # xlScreen, xlPicture (vector): stays sharp when enlarged
    $scale = $TargetWidth / ($range.Width * 96 / 72)
    $w = $range.Width * $scale
    $h = $range.Height * $scale
    $holder = $Sheet.ChartObjects().Add(0, 0, $w, $h)
    try {
        # A fresh, inactive chart sometimes ignores the first paste; activate it and retry until the picture lands.
        for ($attempt = 1; $holder.Chart.Shapes.Count -eq 0; $attempt++) {
            if ($attempt -gt 10) { throw "could not paste the picture of $Address" }
            Start-Sleep -Milliseconds 300
            [void]$holder.Activate()
            [void]$holder.Chart.Paste()
        }
        $holder.Chart.ChartArea.Format.Line.Visible = 0
        $picture = $holder.Chart.Shapes.Item($holder.Chart.Shapes.Count)
        $picture.LockAspectRatio = -1
        $picture.Width = $w
        $picture.Left = 0
        $picture.Top = 0
        $file = Join-Path $OutDir $Name
        [void]$holder.Chart.Export($file, "PNG")
        "  $Name  ($Address)"
    } finally {
        $holder.Delete()
    }
}

function Open-Report($Excel, [string]$Path) {
    $logs = @(Get-ChildItem ([IO.Path]::GetTempPath()) -Filter "error*.xml" -ErrorAction SilentlyContinue).Count
    $book = $Excel.Workbooks.Open($Path, 0, $true)
    $after = @(Get-ChildItem ([IO.Path]::GetTempPath()) -Filter "error*.xml" -ErrorAction SilentlyContinue).Count
    if ($after -ne $logs) { throw "Excel repaired $Path while opening it" }
    Write-Host "  opened without repair: $($book.Worksheets.Count) sheets"
    return $book
}

function Get-LastRow($Sheet) {
    $used = $Sheet.UsedRange
    $last = $used.Row + $used.Rows.Count - 1
    foreach ($chart in $Sheet.ChartObjects()) {
        if ($chart.BottomRightCell.Row -gt $last) { $last = $chart.BottomRightCell.Row }
    }
    return $last
}

function Get-LastColumn($Sheet) {
    $used = $Sheet.UsedRange
    $last = $used.Column + $used.Columns.Count - 1
    foreach ($chart in $Sheet.ChartObjects()) {
        if ($chart.BottomRightCell.Column -gt $last) { $last = $chart.BottomRightCell.Column }
    }
    return $last
}

function Get-Address($Sheet, [int]$LastRow, [int]$LastColumn) {
    return $Sheet.Range($Sheet.Cells(1, 1), $Sheet.Cells($LastRow, $LastColumn)).Address(0, 0)
}

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$numberAsText = $excel.ErrorCheckingOptions.NumberAsText
try {
    "baseline report:"
    $book = Open-Report $excel "$work\baseline\order_quality_report.xlsx"
    $dashboard = $book.Worksheets.Item("Executive Dashboard")
    $lastColumn = Get-LastColumn $dashboard
    Save-RangePicture $dashboard (Get-Address $dashboard (Get-LastRow $dashboard) $lastColumn) "dashboard.png"
    # The cards without the charts: everything above the first chart.
    $cardsEnd = $dashboard.ChartObjects(1).TopLeftCell.Row - 1
    Save-RangePicture $dashboard (Get-Address $dashboard $cardsEnd $lastColumn) "dashboard-summary.png"

    $sheet = $book.Worksheets.Item("Monthly Revenue")
    Save-RangePicture $sheet (Get-Address $sheet (Get-LastRow $sheet) (Get-LastColumn $sheet)) "monthly-revenue.png"
    $sheet = $book.Worksheets.Item("Data Quality Summary")
    Save-RangePicture $sheet (Get-Address $sheet (Get-LastRow $sheet) (Get-LastColumn $sheet)) "data-quality-report.png"
    $sheet = $book.Worksheets.Item("Reconciliation Audit")
    Save-RangePicture $sheet (Get-Address $sheet (Get-LastRow $sheet) (Get-LastColumn $sheet)) "reconciliation-audit.png"
    $sheet = $book.Worksheets.Item("Rejected Records")
    Save-RangePicture $sheet (Get-Address $sheet 24 (Get-LastColumn $sheet)) "rejected-records.png"

    # Duplicate review: keep the last exact-duplicate pair visible, then every conflicting version.
    $sheet = $book.Worksheets.Item("Duplicate Review")
    $firstConflict = $sheet.Columns(2).Find("CONFLICTING_DUPLICATE").Row
    if ($firstConflict -gt 7) { $sheet.Rows("5:$($firstConflict - 3)").Hidden = $true }
    $lastRow = [Math]::Min((Get-LastRow $sheet), $firstConflict + 11)
    Save-RangePicture $sheet (Get-Address $sheet $lastRow (Get-LastColumn $sheet)) "duplicate-review.png"
    $book.Close($false)

    "late-batch report:"
    $book = Open-Report $excel "$work\late-batch\order_quality_report.xlsx"
    $sheet = $book.Worksheets.Item("Correction Log")
    Save-RangePicture $sheet (Get-Address $sheet (Get-LastRow $sheet) (Get-LastColumn $sheet)) "correction-log.png"
    $book.Close($false)

    "raw export:"
    # The first 20 rows of the export, every value written as text exactly as received (spaces and case kept).
    Add-Type -AssemblyName Microsoft.VisualBasic
    $parser = New-Object Microsoft.VisualBasic.FileIO.TextFieldParser("$root\data\raw\orders_export_1.csv",
                                                                      [Text.Encoding]::UTF8)
    $parser.SetDelimiters(",")
    $parser.TrimWhiteSpace = $false
    $book = $excel.Workbooks.Add()
    $sheet = $book.Worksheets.Item(1)
    $sheet.Range("A1:H21").NumberFormat = "@"
    for ($row = 1; $row -le 21 -and -not $parser.EndOfData; $row++) {
        $fields = $parser.ReadFields()
        for ($column = 1; $column -le $fields.Count; $column++) { $sheet.Cells($row, $column).Value2 = $fields[$column - 1] }
    }
    $parser.Close()
    $excel.ErrorCheckingOptions.NumberAsText = $false  # hide the green markers; restored below
    $sheet.Range("A1:H1").Font.Bold = $true
    [void]$sheet.Range("A1:H21").Columns.AutoFit()
    foreach ($column in 1..8) { $sheet.Columns($column).ColumnWidth = $sheet.Columns($column).ColumnWidth + 2 }
    Save-RangePicture $sheet "A1:H21" "raw-export-before.png" 1000
    $book.Close($false)
} finally {
    $excel.ErrorCheckingOptions.NumberAsText = $numberAsText
    $excel.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    Remove-Item -Recurse -Force $work
}

& $Python scripts\strip-png-metadata.py (Get-ChildItem $OutDir -Filter *.png | ForEach-Object { $_.FullName })
if ($LASTEXITCODE -ne 0) { throw "metadata strip failed" }
