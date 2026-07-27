const { startRitmeBackend, stopRitmeBackend, getRitmeStatus } = require('./ritmeLauncher');
const { ipcMain } = require('electron');

let backendStarted = false;

async function initRitme() {
    if (backendStarted) return;
    try {
        console.log('[RITME] Initializing pipeline backend...');
        const port = await startRitmeBackend();
        backendStarted = true;
        console.log(`[RITME] Pipeline backend ready on port ${port}`);
        return port;
    } catch (error) {
        console.error('[RITME] Failed to start:', error.message);
        return null;
    }
}

function registerRitmeIpcHandlers(mainWindow, getState) {
    // Status check
    ipcMain.handle('ritme:status', () => {
        return { ...getRitmeStatus(), envReady: process.env.RITME_BACKEND_DIR ? true : false };
    });

    // API proxy - forwards requests from renderer to RITME backend
    ipcMain.handle('ritme:api', async (event, { method, path, body }) => {
        const status = getRitmeStatus();
        if (!status.running) {
            return { error: 'RITME backend not running' };
        }
        try {
            const fetch = (await import('node-fetch')).default || require('http');
            const url = `http://127.0.0.1:${status.port}${path}`;
            const options = {
                method: method || 'GET',
                headers: { 'Content-Type': 'application/json' },
            };
            if (body && method !== 'GET') {
                options.body = JSON.stringify(body);
            }
            
            const response = await fetch(url, options);
            const data = await response.json();
            return { status: response.status, data };
        } catch (error) {
            return { error: error.message };
        }
    });

    // Health check
    ipcMain.handle('ritme:health', async () => {
        const status = getRitmeStatus();
        if (!status.running) {
            return { ok: false, error: 'Not running' };
        }
        try {
            const http = require('http');
            const url = `http://127.0.0.1:${status.port}/api/setup/check`;
            return new Promise((resolve) => {
                http.get(url, (res) => {
                    let data = '';
                    res.on('data', (chunk) => data += chunk);
                    res.on('end', () => {
                        try {
                            resolve({ ok: true, data: JSON.parse(data) });
                        } catch { resolve({ ok: true, data: null }); }
                    });
                }).on('error', (e) => resolve({ ok: false, error: e.message }));
            });
        } catch (error) {
            return { ok: false, error: error.message };
        }
    });
}

function cleanupRitme() {
    console.log('[RITME] Cleaning up...');
    stopRitmeBackend();
}

module.exports = { initRitme, registerRitmeIpcHandlers, cleanupRitme };
