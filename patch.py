import re
html = open("apps/api/templates/dashboard.html", encoding="utf-8").read()

# The global table section is <section id="global-bottom-explorer" ... if I give it an id, or I can just find it.
# Let's add an id to it!
target_section = '<section class="max-w-7xl w-full mx-auto px-6 pb-8">'
replacement_section = '<section id="global-bottom-explorer" class="max-w-7xl w-full mx-auto px-6 pb-8">'
if target_section in html:
    html = html.replace(target_section, replacement_section)

# Now modify switchMainTab
target_js = "      const titleEl = document.getElementById('current-tab-breadcrumb');"
replacement_js = """      const titleEl = document.getElementById('current-tab-breadcrumb');
      const globalBottom = document.getElementById('global-bottom-explorer');
      if (globalBottom) {
        if (tab === 'inbox') globalBottom.classList.add('hidden');
        else globalBottom.classList.remove('hidden');
      }"""
if target_js in html and "globalBottom.classList" not in html:
    html = html.replace(target_js, replacement_js)

open("apps/api/templates/dashboard.html", "w", encoding="utf-8").write(html)
print("Updated switchMainTab!")
