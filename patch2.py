import re
html = open("apps/api/templates/dashboard.html", encoding="utf-8").read()

html = html.replace('<section id="global-bottom-explorer" class="max-w-7xl w-full mx-auto px-6 pb-8">', '<section id="global-bottom-explorer" class="max-w-7xl w-full mx-auto px-6 pb-8 hidden">')

open("apps/api/templates/dashboard.html", "w", encoding="utf-8").write(html)
print("Added hidden to global bottom explorer!")
