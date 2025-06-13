FROM ubuntu:22.04

# Install Python
RUN apt update && apt install -y python3 python3-pip

# Make python3 the default python command
RUN ln -s /usr/bin/python3 /usr/bin/python

WORKDIR /app
COPY requirements.txt .
RUN pip3 install -r requirements.txt
COPY . .

CMD ["python", "main.py"]
