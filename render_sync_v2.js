    function renderSyncSummary(data) {
      const container = document.getElementById('trace-container');
      const statusEl = document.getElementById('trace-status');
      
      statusEl.innerText = 'Sync Completed';
      statusEl.className = 'text-xs px-2.5 py-0.5 rounded-full bg-[#121212] text-green-400 border border-[#262626]';
      
      // Hide standard single-email trace elements
      Array.from(container.children).forEach(child => {
        if(child.id !== 'sync-summary-result') {
            child.classList.add('hidden');
        }
      });
      
      let summaryDiv = document.getElementById('sync-summary-result');
      if (!summaryDiv) {
          summaryDiv = document.createElement('div');
          summaryDiv.id = 'sync-summary-result';
          summaryDiv.className = 'w-full pb-4';
          container.appendChild(summaryDiv);
      }
      
      summaryDiv.classList.remove('hidden');
      
      if (!data.executions || data.executions.length === 0) {
        summaryDiv.innerHTML = `<div class="h-64 flex flex-col items-center justify-center text-center p-8 border border-dashed border-[#1e1e1e] rounded-lg">
            <div class="w-12 h-12 rounded-full bg-[#121212] flex items-center justify-center text-2xl mb-3">??</div>
            <h3 class="text-sm font-medium text-neutral-300">Inbox Up to Date</h3>
            <p class="text-xs text-neutral-500 max-w-sm mt-1">0 new messages found. All recent inbox messages have already been processed.</p>
          </div>`;
        return;
      }
      
      let html = `<div class="mb-4 bg-[#0a0a0a] border border-[#262626] p-3 rounded-lg flex items-center justify-between shadow-sm">
          <div>
            <h3 class="text-white font-bold text-sm">Automated Agent Sync Complete</h3>
            <p class="text-neutral-400 text-xs mt-0.5">${data.messages_processed} new email(s) processed headlessly</p>
          </div>
          <div class="w-8 h-8 rounded-full bg-[#1a1a1a] flex items-center justify-center text-white font-bold border border-[#333]">${data.messages_processed}</div>
        </div>
        <div class="space-y-3">`;
      
      data.executions.forEach((exec) => {
        const isDup = exec.is_duplicate || exec.status === 'skipped_duplicate';
        const badgeColor = isDup ? 'text-neutral-400 bg-[#121212]' : 'text-green-400 bg-[#0a1a0a] border-green-900/50';
        const badgeText = isDup ? 'Skipped (Duplicate)' : 'Processed';
        const outcomeText = isDup ? 'Email was already processed in a previous sync.' : (exec.terminal_outcome || 'Agent executed successfully');
        
        html += `<div class="bg-black border border-[#1e1e1e] rounded-lg p-3 relative overflow-hidden">
            ${isDup ? '' : '<div class="absolute left-0 top-0 bottom-0 w-0.5 bg-green-500"></div>'}
            <div class="flex items-start justify-between mb-2">
              <div class="flex-1 pr-3">
                <div class="text-[10px] text-neutral-500 font-mono mb-1">MSG: ${exec.message_id.substring(0, 12)}...</div>
                <h4 class="text-sm font-medium text-white line-clamp-1">${exec.subject || 'No Subject'}</h4>
              </div>
              <span class="text-[10px] px-2 py-0.5 rounded border border-[#262626] whitespace-nowrap ${badgeColor}">${badgeText}</span>
            </div>
            
            <div class="mt-2 pt-2 border-t border-[#1a1a1a]">
              <div class="text-xs text-neutral-300 flex items-start">
                <span class="mr-1.5 mt-0.5 text-neutral-500">?</span>
                <span>${outcomeText}</span>
              </div>
              ${exec.matched_entity_id ? `<div class="mt-2 bg-[#0a0a0a] rounded px-2 py-1.5 border border-[#1e1e1e] inline-block">
                  <span class="text-[10px] text-neutral-500 mr-1">Updated Record ID:</span>
                  <span class="text-[10px] text-neutral-300 font-mono">${exec.matched_entity_id}</span>
                </div>` : ''}
            </div>
          </div>`;
      });
      
      html += `</div>`;
      summaryDiv.innerHTML = html;
    }
