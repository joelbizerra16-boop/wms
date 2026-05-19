from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('nf', '0010_entradanf_xml_backup_gzip'),
		('rotas', '0001_initial'),
		('tarefas', '0014_remove_tarefa_tarefa_status_created_idx_and_more'),
	]

	operations = [
		migrations.CreateModel(
			name='OndaSeparacao',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('created_at', models.DateTimeField(auto_now_add=True, verbose_name='criado em')),
				('updated_at', models.DateTimeField(auto_now=True, verbose_name='atualizado em')),
				('codigo', models.CharField(blank=True, max_length=20, unique=True, verbose_name='codigo da onda')),
				('setor', models.CharField(choices=[('AGREGADO', 'Agregado'), ('LUBRIFICANTE', 'Lubrificante'), ('FILTROS', 'Filtros'), ('NAO_ENCONTRADO', 'Nao Encontrado')], max_length=20, verbose_name='setor')),
				('tipo_embalagem', models.CharField(blank=True, db_index=True, default='', max_length=20, verbose_name='tipo de embalagem')),
				('status', models.CharField(choices=[('PENDENTE', 'Pendente'), ('EM_SEPARACAO', 'Em separacao'), ('PARCIAL', 'Parcial'), ('AGUARDANDO_CONFERENCIA', 'Aguardando conferencia'), ('FINALIZADA', 'Finalizada')], db_index=True, default='PENDENTE', max_length=30, verbose_name='status')),
				('nf_total', models.PositiveSmallIntegerField(default=0, verbose_name='total de NFs')),
				('itens_total', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens totais')),
				('itens_bipados', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens bipados')),
				('itens_pendentes', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens pendentes')),
				('percentual', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='percentual')),
				('operador', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='ondas_separacao', to=settings.AUTH_USER_MODEL, verbose_name='operador responsavel')),
				('rota', models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='ondas_separacao', to='rotas.rota', verbose_name='rota')),
			],
			options={
				'verbose_name': 'onda de separacao',
				'verbose_name_plural': 'ondas de separacao',
				'ordering': ('-created_at', '-id'),
			},
		),
		migrations.AddField(
			model_name='ondaseparacao',
			name='nfs',
			field=models.ManyToManyField(blank=True, related_name='ondas_separacao', to='nf.notafiscal', verbose_name='notas fiscais'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='itens_bipados',
			field=models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens bipados'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='itens_pendentes',
			field=models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens pendentes'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='itens_total',
			field=models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='itens totais'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='onda',
			field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='tarefas_operacionais', to='tarefas.ondaseparacao', verbose_name='onda de separacao'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='ordem_na_onda',
			field=models.PositiveSmallIntegerField(default=1, verbose_name='ordem na onda'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='percentual',
			field=models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='percentual'),
		),
		migrations.AddField(
			model_name='tarefa',
			name='tipo_embalagem',
			field=models.CharField(blank=True, db_index=True, default='', max_length=20, verbose_name='tipo de embalagem'),
		),
		migrations.AddIndex(
			model_name='ondaseparacao',
			index=models.Index(fields=['status', 'setor'], name='onda_status_setor_idx'),
		),
		migrations.AddIndex(
			model_name='ondaseparacao',
			index=models.Index(fields=['rota', 'setor', 'tipo_embalagem'], name='onda_rota_setor_emb_idx'),
		),
		migrations.AddIndex(
			model_name='ondaseparacao',
			index=models.Index(fields=['operador', 'status'], name='onda_operador_status_idx'),
		),
		migrations.AddIndex(
			model_name='tarefa',
			index=models.Index(fields=['onda', 'status'], name='tarefa_onda_status_idx'),
		),
		migrations.AddIndex(
			model_name='tarefa',
			index=models.Index(fields=['rota', 'setor', 'tipo_embalagem'], name='tarefa_rota_setor_emb_idx'),
		),
	]