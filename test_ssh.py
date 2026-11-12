import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("Connecting to 93.127.132.66:10051...")
client.connect('93.127.132.66', port=10051, username='administrator', password='7ma6w@ehZc', timeout=15)
print("Connected!")

stdin, stdout, stderr = client.exec_command('echo HELLO_FROM_VPS && whoami && hostname')
out = stdout.read().decode()
err = stderr.read().decode()
print(f"STDOUT: {out}")
print(f"STDERR: {err}")

client.close()
print("Done.")
