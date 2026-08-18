from fastapi import FastAPI, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

test_app = FastAPI(title="Mock Website Target")

@test_app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return RedirectResponse(url="/login")

@test_app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <html>
      <head>
        <title>EMS Login</title>
        <style>
          body { font-family: sans-serif; background: #f3f4f6; display: flex; align-items: center; justify-content: center; height: 100vh; }
          .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px; }
          .group { margin-bottom: 1rem; }
          label { display: block; margin-bottom: 0.5rem; font-weight: bold; }
          input { width: 100%; padding: 0.5rem; border: 1px solid #ccc; border-radius: 4px; }
          button { width: 100%; padding: 0.5rem; background: #3b82f6; color: white; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
        </style>
      </head>
      <body>
        <div class="card">
          <h2>EMS System Login</h2>
          <form action="/login" method="post">
            <div class="group">
              <label for="username">Username</label>
              <input type="text" id="username" name="username" placeholder="admin" required />
            </div>
            <div class="group">
              <label for="password">Password</label>
              <input type="password" id="password" name="password" placeholder="••••••••" required />
            </div>
            <button type="submit">Sign In</button>
          </form>
        </div>
      </body>
    </html>
    """

@test_app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if username == "admin" and password == "admin123":
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value="mock_session_active")
        return response
    return HTMLResponse(
        content="""
        <html><body><h3>Login Failed</h3><a href="/login">Try again</a></body></html>
        """, 
        status_code=401
    )

@test_app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    cookie = request.cookies.get("session_token")
    if cookie != "mock_session_active":
         return RedirectResponse(url="/login")
         
    return """
    <html>
      <head>
        <title>EMS Dashboard</title>
        <style>
          body { font-family: sans-serif; margin: 0; padding: 2rem; background: #fafafa; }
          nav { background: #333; color: white; padding: 1rem; margin-bottom: 2rem; display: flex; gap: 1rem; }
          nav a { color: white; text-decoration: none; }
          table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
          th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
          th { background: #eee; }
        </style>
      </head>
      <body>
        <nav>
          <strong>EMS Portal</strong>
          <a href="/dashboard">Dashboard</a>
          <a href="/profile">Profile</a>
          <a href="/settings">Settings</a>
          <a href="/logout">Logout</a>
        </nav>
        
        <h2>Employee List</h2>
        <table>
          <thead>
            <tr><th>Name</th><th>Role</th><th>Email</th><th>Actions</th></tr>
          </thead>
          <tbody>
            <tr><td>Alice Vance</td><td>QA Lead</td><td>alice@test.com</td><td><button>Edit</button> <button>Delete</button></td></tr>
            <tr><td>Bob Smith</td><td>Developer</td><td>bob@test.com</td><td><button>Edit</button> <button>Delete</button></td></tr>
          </tbody>
        </table>
        
        <h3 style="margin-top: 2rem;">Add New Employee</h3>
        <form action="/add-employee" method="post" style="max-width: 300px;">
          <input type="text" name="name" placeholder="Full Name" required style="margin-bottom: 0.5rem; display: block; width: 100%;" />
          <input type="email" name="email" placeholder="Email" required style="margin-bottom: 0.5rem; display: block; width: 100%;" />
          <button type="submit">Add User</button>
        </form>
      </body>
    </html>
    """

@test_app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    cookie = request.cookies.get("session_token")
    if cookie != "mock_session_active":
         return RedirectResponse(url="/login")
    return """
    <html>
      <head><title>EMS Profile</title></head>
      <body>
        <h2>User Profile</h2>
        <p>Username: admin</p>
        <p>Access Level: Super Administrator</p>
        <a href="/dashboard">Back to Dashboard</a>
      </body>
    </html>
    """

@test_app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    cookie = request.cookies.get("session_token")
    if cookie != "mock_session_active":
         return RedirectResponse(url="/login")
    return """
    <html>
      <head><title>EMS Settings</title></head>
      <body>
        <h2>Portal Settings</h2>
        <form action="/save-settings" method="post">
          <label><input type="checkbox" name="notifs" checked /> Enable Email Notifications</label><br/><br/>
          <button type="submit">Save Changes</button>
        </form>
        <br/>
        <a href="/dashboard">Back to Dashboard</a>
      </body>
    </html>
    """

@test_app.get("/logout")
def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_token")
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(test_app, host="0.0.0.0", port=8080)
