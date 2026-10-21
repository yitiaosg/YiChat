document.querySelectorAll('.login-devices time[datetime]').forEach(node=>{const date=new Date(node.dateTime);if(!Number.isNaN(date.getTime()))node.textContent=date.toLocaleString()});
