-- FIXTURE SIMULADA para testes do wow-collection-planner.
-- Estrutura baseada no código do AllTheThings 5.4.5 (ATTCharacterData indexado por GUID,
-- caches Mounts/BattlePets/Toys/Titles/Achievements/Factions/Sources/Spells). IDs e nomes são falsos.

AllTheThingsSettings = {
	["General"] = {
		["AccountMode"] = true,
		["Completionist"] = true,
	},
	["Version"] = "SIMULADO-0.0.0",
}
ATTAccountWideData = {
	["Achievements"] = {
		[1001] = 1,
		[1002] = 3,
		[1003] = 0,
	},
	["Titles"] = {
		[42] = 1,
	},
	["Toys"] = {
		[5001] = { ["name"] = "Brinquedo \"Simulado\" de Teste", ["collected"] = true },
		[5002] = { ["name"] = "Outro Brinquedo", ["collected"] = false },
	},
	["Factions"] = {
		76, -- lista sequencial: vira lista de ids
		530,
	},
	["Mounts"] = {
		[580] = 2, -- 2 = obtido por outro personagem da conta
	},
	["BattlePets"] = {
	},
	["HexValue"] = 0x1F,
	["Note"] = [[texto
longo]],
}
ATTCharacterData = {
	["Player-0000-0000AAAA"] = {
		["name"] = "Zarnok",
		["realm"] = "Forever",
		["race"] = "Undead",
		["class"] = "Mage",
		["lvl"] = 12,
		["Factions"] = {
			[68] = 1,
		},
		["Titles"] = {
			[7] = 1,
		},
	},
	["Player-0000-0000BBBB"] = {
		["name"] = "Desconhecido",
		["realm"] = "Forever",
		["race"] = "Orc",
		["class"] = "Warrior",
		["Factions"] = {
			[999] = 1,
		},
	},
}
ATTBroken = {
	["sem fechar" =
}
ATTAfterBroken = {
	["ok"] = true,
}
