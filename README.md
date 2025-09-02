# Accounting App



## Getting Started

### Create a Virtual Environment

1. **Navigate to your project directory**:

   ```bash
   cd /path/to/your/project
   ```

2. **Create a virtual environment**:

   ```bash
   python3.12 -m venv .venv
   ```

   This will create a `.venv` directory in your project folder.

3. **Activate the virtual environment**:

   * On **Linux/macOS**:

     ```bash
     source .venv/bin/activate
     ```

   * On **Windows**:

     ```bash
     .venv\Scripts\Activate.ps1
     ```

   After activation, your prompt should change to indicate the active environment, e.g., `(.venv)`.

4. **Upgrade `pip`, `setuptools`, and `wheel`**:

   ```bash
   pip install --upgrade pip setuptools wheel
   ```

### Create Django app

1. Open VS Code Terminal

* In VS Code, open the integrated terminal (`Ctrl + backtick` `` ` ``).
* Make sure you’re in your Django project root (where `manage.py` is).

---

2. Run `startapp` to create your new app

For your example (`accounts_core`):

```bash
python manage.py startapp accounts_core
```

3. Register the app in `settings.py`

Open `yourproject/settings.py`, find `INSTALLED_APPS = [...]`, and add your app:

```python
INSTALLED_APPS = [
...
    'accounts_core', 
]
```

---

4. Verify it’s working

Run:

```bash
python manage.py makemigrations
python manage.py migrate
```

### Run tasks automatically


#### 1. Install Celery + Beat

In your project’s virtualenv:

```bash
pip install celery[redis]
```

* `celery` = the task queue
* `[redis]` = adds Redis transport support (most common broker/backend)

You’ll also need a broker (Redis is simplest):

```bash
sudo apt install redis-server
```

Start Redis:

```bash
redis-server
```

---

#### 2. Configure Celery

- Update your `settings.py`
- Update `__init__.py` of your project (next to `settings.py`)


#### 3. Write your task

In `ac_project/tasks.py`


---

#### 4. Run Celery workers + Beat

You need **two processes** running, run both in one combined process:

```bash
celery -A ac_project worker -B -l info
```


✅ Now Celery Beat will fire your task, and the Celery worker will execute it.


