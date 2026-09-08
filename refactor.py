import re

html = open("apps/api/templates/dashboard.html", encoding="utf-8").read()

start = html.find("<div id=\"section-inbox\"")
end = html.find("<!-- TAB 2: CANONICAL MODEL -->")

new_inbox = """      <!-- TAB 1: INBOX AGENT -->
      <div id="section-inbox" class="flex flex-col gap-6" style="min-height: calc(100vh - 120px);">
        <!-- Top Bar: Action & Connection Status -->
        <div class="flex flex-col sm:flex-row items-center justify-between bg-[#0a0a0a] border border-[#1e1e1e] rounded-xl p-5 shadow-sm shrink-0">
          <div class="flex items-center space-x-4 mb-4 sm:mb-0">
            <div class="flex items-center justify-center w-10 h-10 rounded-full bg-[#121212] border border-[#262626]">
              <span class="text-xl">??</span>
            </div>
            <div>
              <h2 class="text-base font-semibold text-white">Inbox Agent Overview</h2>
              <div id="gmail-status-pill" class="text-xs text-neutral-500 mt-0.5">Connected and monitoring</div>
            </div>
          </div>
          <div class="flex items-center space-x-4">
             <div id="last-synced-time" class="text-xs text-neutral-500 hidden sm:block">Last Synced: Just now</div>
             <button onclick="syncLiveGmail()" id="btn-sync-gmail" class="px-5 py-2.5 rounded-lg bg-white hover:bg-neutral-200 text-black text-sm font-semibold transition flex items-center justify-center shadow-lg shadow-white/10">
               <span class="mr-2">?</span> Sync Live Inbox
             </button>
          </div>
        </div>

        <!-- Main Split: Live Shipments (Left) & Activity Feed (Right) -->
        <div class="flex flex-col lg:flex-row flex-1 overflow-hidden gap-6">
          <!-- Left: Shipments Data (Supabase) -->
          <div class="flex-[2] flex flex-col bg-[#0a0a0a] border border-[#1e1e1e] rounded-xl shadow-sm overflow-hidden min-h-[500px]">
            <div class="border-b border-[#1e1e1e] bg-[#050505] px-5 py-4 flex items-center justify-between shrink-0">
               <h2 class="text-sm font-semibold text-white flex items-center">
                  <span class="mr-2">???</span> Supabase Live Shipments Database
               </h2>
               <button onclick="refreshTables()" class="px-3 py-1.5 rounded-md bg-[#121212] hover:bg-[#1a1a1a] border border-[#262626] text-neutral-300 transition flex items-center text-xs font-medium">
                 <span class="mr-1">?</span> Refresh Data
               </button>
            </div>
            <div class="flex-1 overflow-auto bg-black p-0 relative">
               <table class="w-full text-left border-collapse min-w-[700px]">
                 <thead class="sticky top-0 bg-[#050505] z-10 shadow-sm border-b border-[#1e1e1e]">
                   <tr class="text-neutral-400 text-[10px] uppercase tracking-wider">
                     <th class="px-5 py-3 font-medium">Load ID</th>
                     <th class="px-5 py-3 font-medium">Shipment #</th>
                     <th class="px-5 py-3 font-medium">Status</th>
                     <th class="px-5 py-3 font-medium">Carrier</th>
                     <th class="px-5 py-3 font-medium">Route</th>
                     <th class="px-5 py-3 font-medium">Pickup Date</th>
                   </tr>
                 </thead>
                 <tbody id="inbox-shipments-tbody" class="text-xs text-neutral-300">
                   <tr><td colspan="6" class="text-center py-8 text-neutral-500">Loading live data...</td></tr>
                 </tbody>
               </table>
            </div>
          </div>

          <!-- Right: Inbox Agent Activity Feed -->
          <div class="flex-1 flex flex-col bg-[#0a0a0a] border border-[#1e1e1e] rounded-xl shadow-sm overflow-hidden min-h-[500px] min-w-[320px]">
            <div class="border-b border-[#1e1e1e] bg-[#050505] px-5 py-4 flex items-center justify-between shrink-0">
               <h2 class="text-sm font-semibold text-white flex items-center">
                  <span class="mr-2">??</span> AI Agent Activity
               </h2>
               <span id="trace-status" class="text-[10px] px-2.5 py-1 rounded-full bg-[#121212] text-neutral-400 border border-[#262626]">Awaiting Sync</span>
            </div>
            <div id="trace-container" class="flex-1 overflow-y-auto p-4 bg-black relative">
               <div id="sync-summary-result" class="w-full">
                 <div class="h-full min-h-[300px] flex flex-col items-center justify-center text-center p-8 border border-dashed border-[#1e1e1e] rounded-lg mt-4">
                   <div class="w-12 h-12 rounded-full bg-[#121212] flex items-center justify-center text-2xl mb-3 border border-[#262626]">??</div>
                   <h3 class="text-sm font-medium text-neutral-300">Ready to Process</h3>
                   <p class="text-xs text-neutral-500 mt-2 max-w-[200px] mx-auto leading-relaxed">Click 'Sync Live Inbox' above to fetch and process new emails automatically.</p>
                 </div>
               </div>
            </div>
          </div>
        </div>
      </div>\n\n"""

new_html = html[:start] + new_inbox + html[end:]

# Now patch refreshTables()
target = "const shipBody = document.getElementById('shipments-tbody');"
replacement = "const shipBody = document.getElementById('shipments-tbody');\n        const shipBodyInbox = document.getElementById('inbox-shipments-tbody');"

target2 = "shipBody.innerHTML = shipments.map(s => `"
replacement2 = "const htmlRows = shipments.map(s => `\n            <tr class=\"hover:bg-[#121212] transition\">\n              <td class=\"py-3 px-5 font-semibold text-white\">${s.load_id || '-'}</td>\n              <td class=\"py-3 px-5 font-mono text-neutral-400\">${s.shipment_number}</td>\n              <td class=\"py-3 px-5\">\n                <span class=\"px-2 py-0.5 rounded text-[11px] font-medium ${getStatusClass(s.status)}\">\n                  ${s.status}\n                </span>\n              </td>\n              <td class=\"py-3 px-5 text-neutral-400\">${s.carrier_id || '-'}</td>\n              <td class=\"py-3 px-5 text-neutral-400\">\n                ${s.origin_city ? s.origin_city.substring(0, 8) + ' ? ' + (s.dest_city ? s.dest_city.substring(0, 8) : '') : '-'}\n              </td>\n              <td class=\"py-3 px-5 font-mono text-neutral-400\">${s.pickup_date ? s.pickup_date.split('T')[0] : '-'}</td>\n            </tr>\n          `).join('');\n          shipBody.innerHTML = htmlRows;\n          if (shipBodyInbox) shipBodyInbox.innerHTML = htmlRows;"

new_html = new_html.replace(target, replacement)

# We need to replace the entire shipments.map(s => `...`) assignment
map_start = new_html.find("shipBody.innerHTML = shipments.map(s => `")
map_end = new_html.find("`).join('');", map_start) + 12

if map_start != -1 and map_end != -1:
    new_html = new_html[:map_start] + replacement2 + new_html[map_end:]

open("apps/api/templates/dashboard.html", "w", encoding="utf-8").write(new_html)
print("Dashboard updated!")
