
FROM python:3.13-alpine

# 2. Set the internal working directory
WORKDIR /app

# 3. Copy only dependency files first to leverage Docker layer caching
COPY requirements.txt .

# 4. Install dependencies without saving local package caches
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the remaining application source code
COPY . .

# 6. Define the command to execute your application
CMD ["python", "app.py"]
