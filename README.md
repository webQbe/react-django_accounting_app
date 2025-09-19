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



### Set up Django Admin to view and manage models

#### 1. Register models in `admin.py`
- This makes both models appear in the Admin sidebar.


#### Delete broken migration history (Optional)

Run:
```bash
rm db.sqlite3
find . -path "*/migrations/*.py" -not -name "__init__.py" -delete
```

#### 2. Make sure your custom user is active

- Add to `settings.py`:
```bash
AUTH_USER_MODEL = "accounts_core.User"
```

#### 3. Create initial migrations for your app

Run:
```bash
python manage.py makemigrations accounts_core
```

#### 4. Apply them in the right order

Run:
```bash
python manage.py migrate
```

#### 5. Make the user manager (`TenantManager()`) inherits from `BaseUserManager`

- Make sure custom user model's manager subclasses `django.contrib.auth.base_user.BaseUserManager` (this gives you `get_by_natural_key` and the usual `create_user` / `create_superuser` behavior).

#### 6. Create a superuser

Run:
```bash
python manage.py createsuperuser
```

#### 7. Try it out

1. Start the dev server
Run:
```bash
python manage.py runserver
```
2. Log into Admin:
    - Open `http://127.0.0.1:8000/admin/`
    - Log in with the superuser you just created.
    - You should see all the models you registered in `admin.py`.
3. Create a Company
4. Add a Currency
5. Add a Customer tied to that company.
6. Try creating an Invoice — `clean()` and `save()` methods will enforce rules 
  *(e.g., no negative totals, no deleting invoices with payments, etc.).*


## Database: Backup, Reset, and Migrations

### Backup

* **Development DB (SQLite / Postgres local):**

  ```bash
      # Postgres (local)
      pg_dump -U <username> acdb > backup.sql

      # If using Docker
      docker exec -t ac-postgres pg_dump -U <username> acdb > backup.sql
  ```

### Resetting the Database

Use this if you need a fresh start in development (⚠️ will delete all data).

```bash
# Drop all tables and recreate from migrations
python manage.py flush     # resets data, keeps schema

# OR drop the whole DB (Postgres example)
dropdb acdb
createdb acdb
python manage.py migrate

# If using Docker, reset the database with:
docker exec -it ac-postgres dropdb -U <username> acdb
docker exec -it ac-postgres createdb -U <username> acdb
python manage.py migrate
```

### Applying Migrations

```bash
# Make new migrations after model changes
python manage.py makemigrations

# Apply migrations
python manage.py migrate
```

### Rolling Back a Migration

```bash
# Migrate to a previous state (e.g., app_name to migration 0005)
python manage.py migrate app_name 0005
```