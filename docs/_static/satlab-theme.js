// Hand the app's theme to Furo, so the docs open in the theme the simulator uses (dark by default).
// The app stores 'light' | 'dark' | 'system' under satlab.theme.v2 (frontend/src/lib/theme.ts);
// Furo reads 'light' | 'dark' | 'auto' from `theme` in its first body script. Only a change of the
// app's choice is copied, so a choice made in the docs sticks until the app's changes again.
;(function () {
  try {
    var app = localStorage.getItem('satlab.theme.v2')
    if (app !== 'light' && app !== 'dark' && app !== 'system') app = 'dark'
    if (localStorage.getItem('satlab.docs.themeFrom') !== app) {
      localStorage.setItem('theme', app === 'system' ? 'auto' : app)
      localStorage.setItem('satlab.docs.themeFrom', app)
    }
  } catch (e) {
    // storage unavailable (private mode): Furo follows the system theme
  }
})()
