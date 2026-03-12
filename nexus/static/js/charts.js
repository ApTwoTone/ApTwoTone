/**
 * Nexus CRM — Canvas Chart Helpers
 * Pure canvas drawing, no external libraries.
 */
var NexusCharts = {

    /**
     * Draw a donut chart.
     * @param {string} canvasId
     * @param {Array<{label: string, value: number, color: string}>} data
     * @param {string} [centerText] - Big text in center
     * @param {string} [centerSub]  - Small text under center
     */
    drawDonut: function(canvasId, data, centerText, centerSub) {
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var w = canvas.clientWidth;
        var h = canvas.clientHeight;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, w, h);

        var total = 0;
        for (var i = 0; i < data.length; i++) total += data[i].value;
        if (total === 0) {
            ctx.beginPath();
            ctx.arc(w/2, h/2, Math.min(w,h)/2 - 10, 0, Math.PI*2);
            ctx.strokeStyle = '#2a3550';
            ctx.lineWidth = 18;
            ctx.stroke();
            if (centerText) {
                ctx.fillStyle = '#64748b';
                ctx.font = 'bold 20px Inter, sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText(centerText, w/2, h/2 + 6);
            }
            return;
        }

        var cx = w / 2;
        var cy = h / 2;
        var radius = Math.min(w, h) / 2 - 10;
        var lineW = 18;
        var angle = -Math.PI / 2;

        for (var i = 0; i < data.length; i++) {
            if (data[i].value === 0) continue;
            var slice = (data[i].value / total) * Math.PI * 2;
            ctx.beginPath();
            ctx.arc(cx, cy, radius, angle, angle + slice);
            ctx.strokeStyle = data[i].color;
            ctx.lineWidth = lineW;
            ctx.lineCap = 'butt';
            ctx.stroke();
            angle += slice;
        }

        // Center text
        if (centerText) {
            ctx.fillStyle = '#e2e8f0';
            ctx.font = 'bold 22px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(centerText, cx, centerSub ? cy - 8 : cy);
        }
        if (centerSub) {
            ctx.fillStyle = '#64748b';
            ctx.font = '500 10px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(centerSub, cx, cy + 14);
        }
    },

    /**
     * Draw a horizontal bar chart.
     * @param {string} canvasId
     * @param {Array<{label: string, value: number, color: string}>} data
     */
    drawBars: function(canvasId, data) {
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var w = canvas.clientWidth;
        var h = canvas.clientHeight;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, w, h);

        if (!data.length) return;
        var maxVal = 0;
        for (var i = 0; i < data.length; i++) {
            if (data[i].value > maxVal) maxVal = data[i].value;
        }
        if (maxVal === 0) maxVal = 1;

        var barH = Math.min(24, (h - 8) / data.length - 4);
        var labelW = 80;
        var barAreaW = w - labelW - 40;
        var y = 4;

        for (var i = 0; i < data.length; i++) {
            // Label
            ctx.fillStyle = '#94a3b8';
            ctx.font = '500 10px Inter, sans-serif';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            ctx.fillText(data[i].label, labelW - 6, y + barH / 2);

            // Bar background
            ctx.fillStyle = '#1e293b';
            ctx.beginPath();
            ctx.roundRect(labelW, y, barAreaW, barH, 4);
            ctx.fill();

            // Bar fill
            var fillW = (data[i].value / maxVal) * barAreaW;
            if (fillW > 0) {
                ctx.fillStyle = data[i].color;
                ctx.globalAlpha = 0.7;
                ctx.beginPath();
                ctx.roundRect(labelW, y, Math.max(fillW, 8), barH, 4);
                ctx.fill();
                ctx.globalAlpha = 1;
            }

            // Value
            ctx.fillStyle = '#e2e8f0';
            ctx.font = 'bold 10px Inter, sans-serif';
            ctx.textAlign = 'left';
            ctx.fillText(String(data[i].value), labelW + barAreaW + 6, y + barH / 2);

            y += barH + 4;
        }
    },
};
