function filterCards(){
  const q=(document.getElementById('search')?.value||'').toLowerCase();
  document.querySelectorAll('#cards .person-card').forEach(c=>{
    c.style.display=c.dataset.name.includes(q)?'flex':'none';
  });
}
function filterVehicles(){
  const q=(document.getElementById('search')?.value||'').toLowerCase();
  document.querySelectorAll('#vehicleCards .vehicle-card').forEach(c=>{
    c.style.display=c.dataset.name.includes(q)?'flex':'none';
  });
}
