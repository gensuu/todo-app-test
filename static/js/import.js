// static/js/import.js
// Assumes utils.js (showLoadingOverlay) is loaded before this script.

document.addEventListener('DOMContentLoaded', () => {
    const importForm = document.getElementById('import-form');

    if (importForm) {
        importForm.addEventListener('submit', (e) => {
            const fileInput = document.getElementById('excel_file');
            // Show loading overlay only if a file is actually selected
            if (fileInput && fileInput.files && fileInput.files.length > 0) {
                 // Check if showLoadingOverlay function exists
                 if (typeof showLoadingOverlay === 'function') {
                    showLoadingOverlay('Excelファイルをインポート中...');
                } else {
                    console.error("showLoadingOverlay function is not defined.");
                    // Optionally provide a basic fallback alert
                    // alert("インポート中...");
                }
            } else {
                // Prevent form submission if no file is selected (though 'required' attribute should handle this)
                console.log("No file selected for import.");
                e.preventDefault(); // Stop submission
                alert("Excelファイルを選択してください。"); // Basic feedback
            }
        });
    }
});
